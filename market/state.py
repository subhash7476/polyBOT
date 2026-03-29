import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional

from feeds.weather_types import WeatherForecast


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
    volume_usd: float = 0.0
    bid_depth: float = 0.0   # total size on best 5 bid levels (USDC)
    ask_depth: float = 0.0   # total size on best 5 ask levels (USDC)
    condition_id: str = ""        # on-chain condition ID from Gamma API (used for redemption)
    neg_risk: bool = False       # negRisk market (temperature buckets, etc.)
    fees_enabled: bool = True    # False for negRisk weather markets

    @property
    def mid(self) -> float:
        return (self.best_bid + self.best_ask) / 2

    @property
    def no_best_ask(self) -> float:
        return 1 - self.best_bid  # NO ask = 1 - YES bid


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
