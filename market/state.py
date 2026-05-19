import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from feeds.weather_types import WeatherForecast


@dataclass
class FalconWalletStats:
    """Deep analytics for one tracked whale wallet (Wallet 360, agent 581)."""
    wallet: str
    # Performance
    total_pnl: float = 0.0         # USD PnL over window
    roi: float = 0.0               # ROI as fraction (0.25 = 25%)
    win_rate: float = 0.5          # fraction of trades profitable
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0      # fraction (negative, e.g. -0.18)
    total_trades: int = 0
    markets_traded: int = 0
    # Risk / quality flags (from Wallet 360)
    combined_risk_score: float = 0.0   # 0–1; higher = riskier/suspicious
    risk_level: str = "unknown"        # "Low" / "Medium" / "High"
    sybil_risk_flag: bool = False      # True if bot/sybil behaviour detected
    performance_trend: str = "unknown" # "improving" / "declining" / "stable"
    edge_decay: float = 0.0            # positive = edge declining over time
    # Leaderboard fields (from agent 584)
    h_score: float = 0.0              # Falcon composite score
    trajectory: str = ""              # "rising" / "falling" / "stable"
    leaderboard_rank: int = 0
    fetched_at: float = 0.0

    @property
    def trust_score(self) -> float:
        """Composite: win_rate × (1 + roi), penalised by drawdown and sybil risk.
        Values above 1.0 indicate above-average traders; < 0.5 = poor quality."""
        base = self.win_rate * (1.0 + max(self.roi, 0.0)) * (1.0 - min(abs(self.max_drawdown), 0.9))
        # Halve trust if sybil flag is set
        if self.sybil_risk_flag:
            base *= 0.5
        return base

    @property
    def is_elite(self) -> bool:
        return self.trust_score >= 1.5 and not self.sybil_risk_flag


@dataclass
class FalconMarketInsight:
    """Activity and whale intelligence for one Polymarket market (agent 575)."""
    condition_id: str
    question: str = ""
    slug: str = ""
    end_date: str = ""
    current_volume_24h: float = 0.0
    current_volume_7d: float = 0.0
    volume_trend: str = "Normal"      # "Spiking" / "Normal" / "Declining" / "Dying Interest" / "No Trades"
    liquidity_tier: str = "Low"       # "Low" / "Medium" / "High"
    liquidity_percentile: float = 0.0
    top1_wallet_pct: float = 0.0      # % of volume owned by top-1 wallet
    top3_wallet_pct: float = 0.0
    whale_control_flag: bool = False  # True if top-1 wallet > threshold
    unique_traders_7d: int = 0
    trades_per_hour_avg: float = 0.0
    trade_concentration_flag: bool = False
    fetched_at: float = 0.0


@dataclass
class FeedState:
    """All feed-derived values. Feeds write here; engine reads here."""

    # === Per-asset dictionaries (v3) ===
    spot_prices: dict = field(default_factory=dict)    # "BTC" → 85000.0
    dvol: dict = field(default_factory=dict)           # "BTC" → 72.0
    funding_rates: dict = field(default_factory=dict)  # "BTC" → 0.0001
    vol_skew: dict = field(default_factory=dict)       # "BTC" → 2.3

    # === Macro / on-chain (not per-asset) ===
    btc_exchange_netflow: Optional[float] = None  # normalised [0,1]
    stablecoin_supply_change: Optional[float] = None  # 30-day signal [-1,1]
    btc_hashrate_trend: Optional[float] = None       # 30-day signal [-1,1]
    # Macro consensus forecasts (populated by feeds/macro.py via FRED)
    consensus_cpi: Optional[float] = None           # e.g. 3.2 (%)
    consensus_unemployment: Optional[float] = None  # e.g. 4.0 (%)
    consensus_nfp: Optional[float] = None           # e.g. 200000 (jobs)
    consensus_gdp: Optional[float] = None           # e.g. 2.1 (%)
    dxy: Optional[float] = None
    dxy_confidence: float = 0.0
    dxy_trend: Optional[float] = None   # (dxy - ma20) / ma20
    yield_2y: Optional[float] = None
    yield_10y: Optional[float] = None
    yield_10y_confidence: float = 0.0
    fed_may_cut_prob: Optional[float] = None
    fed_confidence: float = 0.0
    fed_expected_cuts: Optional[float] = None   # Poisson λ for annual cut-count markets
    sofr: Optional[float] = None                # NY Fed SOFR overnight rate

    weather_forecasts: dict = field(default_factory=dict)  # city_slug → WeatherForecast

    # === Falcon API data ===
    falcon_whale_stats: dict = field(default_factory=dict)      # wallet → FalconWalletStats
    falcon_market_insights: dict = field(default_factory=dict)  # condition_id → FalconMarketInsight
    falcon_insights_by_question: dict = field(default_factory=dict)  # question.lower() → FalconMarketInsight

    # Category-specific priors from optional external providers.
    # Shape: category -> lookup_key -> {"prob": float, "confidence": float, "source": str}
    # The key is usually an asset ticker or normalized question text.
    category_priors: dict = field(default_factory=dict)

    # === Feed staleness tracking ===
    last_feed_update: dict = field(default_factory=dict)  # feed_name → unix timestamp (float)

    def is_fresh(self, max_age_seconds: float = 5.0) -> bool:
        """True only if both 'clob' and 'microstructure' feeds updated within max_age_seconds."""
        required = ("clob", "microstructure")
        now = time.time()
        return all(
            now - self.last_feed_update.get(k, 0) < max_age_seconds
            for k in required
        )

    # === Backward-compatible properties (keep until all callers migrated) ===
    @property
    def btc_price(self) -> Optional[float]:
        return self.spot_prices.get("BTC")

    @property
    def eth_price(self) -> Optional[float]:
        return self.spot_prices.get("ETH")

    @property
    def btc_dvol(self) -> Optional[float]:
        return self.dvol.get("BTC")

    @property
    def eth_dvol(self) -> Optional[float]:
        return self.dvol.get("ETH")

    @property
    def btc_vol_skew(self) -> Optional[float]:
        return self.vol_skew.get("BTC")

    @property
    def btc_funding_rate(self) -> Optional[float]:
        return self.funding_rates.get("BTC")


@dataclass
class BalanceState:
    session_start: float = 0.0   # USDC at bot startup
    current: float = 0.0         # latest fetched balance
    last_update: float = 0.0     # unix timestamp of last fetch

    @property
    def session_pnl(self) -> float:
        return self.current - self.session_start


@dataclass
class ContractState:
    """Live CLOB state for a single Polymarket market."""
    yes_token_id: str
    no_token_id: str
    question: str
    category: str
    best_bid: float = 0.0       # YES bid
    best_ask: float = 1.0       # YES ask
    volume_usd: float = 0.0       # total historical volume (USDC)
    volume_24h: float = 0.0       # 24h CLOB volume — reflects current activity
    bid_depth: float = 0.0   # total size on best 5 bid levels (USDC)
    ask_depth: float = 0.0   # total size on best 5 ask levels (USDC)
    condition_id: str = ""        # on-chain condition ID from Gamma API (used for redemption)
    neg_risk: bool = False       # negRisk market (temperature buckets, etc.)
    fees_enabled: bool = True    # False for negRisk weather markets
    end_date_iso: str = ""       # ISO 8601 resolution date from Gamma API (e.g. "2026-05-01T12:00:00Z")
    # Regime score inputs — written by VPINPoller
    vpin: float = 0.5             # size-weighted EMA-smoothed order flow imbalance; 0.5 = neutral
    vpin_updated_at: float = 0.0  # unix timestamp of last VPINPoller write
    # Liquidity reward eligibility — fetched from CLOB API by MarketSelector; 0.0 = not yet fetched
    min_incentive_size: float = 0.0    # minimum order size in shares to qualify for rewards
    max_incentive_spread: float = 0.0  # max distance from mid in [0,1] space; orders beyond score 0

    @property
    def mid(self) -> float:
        return (self.best_bid + self.best_ask) / 2

    @property
    def no_best_ask(self) -> float:
        return 1 - self.best_bid  # NO ask = 1 - YES bid

    @property
    def hours_to_resolution(self) -> float:
        """Hours until market resolves. Defaults to 48h if end_date_iso unset."""
        if not self.end_date_iso:
            return 48.0
        try:
            end = datetime.fromisoformat(self.end_date_iso.replace("Z", "+00:00"))
            hours = (end - datetime.now(timezone.utc)).total_seconds() / 3600
            return max(0.0, min(168.0, hours))
        except (ValueError, TypeError):
            return 48.0


class AppState:
    """Single shared mutable state. All mutations go through async methods."""

    def __init__(self):
        self.feeds = FeedState()
        self.markets: dict[str, ContractState] = {}  # keyed by yes_token_id
        self.balance = BalanceState()
        self._lock = asyncio.Lock()

    async def update_feeds(self, **kwargs):
        """Legacy feed update via setattr — for macro/on-chain feeds that use named fields."""
        async with self._lock:
            for k, v in kwargs.items():
                setattr(self.feeds, k, v)

    async def update_asset_feed(self, asset: str, **kwargs):
        """Update per-asset feed data.
        Example: update_asset_feed("SOL", spot=140.0, funding_rate=0.0002)
        """
        async with self._lock:
            if "spot" in kwargs:
                self.feeds.spot_prices[asset] = kwargs["spot"]
            if "dvol" in kwargs:
                self.feeds.dvol[asset] = kwargs["dvol"]
            if "funding_rate" in kwargs:
                self.feeds.funding_rates[asset] = kwargs["funding_rate"]
            if "vol_skew" in kwargs:
                self.feeds.vol_skew[asset] = kwargs["vol_skew"]

    async def upsert_market(self, state: ContractState):
        async with self._lock:
            self.markets[state.yes_token_id] = state

    async def remove_market(self, yes_token_id: str):
        async with self._lock:
            self.markets.pop(yes_token_id, None)

    def stamp_feed(self, name: str) -> None:
        """Record that a feed produced a live update (call without holding the lock)."""
        import time
        self.feeds.last_feed_update[name] = time.time()
