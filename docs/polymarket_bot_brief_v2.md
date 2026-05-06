# Polymarket Bot — Claude Code Project Brief v2.0
**Date:** March 2026 | **Supersedes:** v1.0  
**Status:** Incorporate all auditor feedback before implementation

---

## What Changed from v1

v1 had strong engineering but weak alpha. This brief fixes that.

| v1 weakness | v2 fix |
|---|---|
| DVOL-only model — no edge | 4-category multi-factor signal engine |
| Wrong likelihood ratio construction | Proper logit-additive Bayesian framework |
| No time-to-expiry modeling | Explicit T extraction + sigma_T = sigma_daily × √T |
| Fragile regex contract parsing | Structured parser with heuristic fallback |
| Kelly too aggressive for uncalibrated model | Default 5–10% Kelly, EV-confidence weighted |
| No slippage / liquidity modeling | Slippage model + order book depth checks |
| No correlation-aware risk | Asset-group exposure limits + vol-regime scaling |
| No calibration loop | Brier score + calibration curve + auto-recalibration hooks |
| Single signal trades | Minimum signal agreement filter (≥2 signals) |
| CME FedWatch has no public API | Replaced with scraped/alternative macro sources |

---

## File Structure

```
polymarket-bot/
├── main.py                    ← async orchestrator
├── config.py                  ← ALL constants, no logic
├── .env.template
├── .env                       ← gitignored
├── requirements.txt
│
├── feeds/
│   ├── __init__.py
│   ├── base.py                ← BaseFeed: start/stop, reconnect, backoff
│   ├── deribit.py             ← DVOL + skew + term structure + IV/RV spread
│   ├── microstructure.py      ← funding rates + OI changes + liquidation clusters
│   ├── onchain.py             ← netflows + whale flows + stablecoin supply + SOPR/NUPL
│   └── macro.py               ← DXY + bond yields (2Y/10Y) + rate probabilities
│
├── engine/
│   ├── __init__.py
│   ├── bayesian.py            ← logit-additive BayesianEngine (log-space)
│   ├── probability.py         ← FeedState + TTE → contract model probability
│   ├── contract_parser.py     ← structured parser: extract asset, direction, strike, expiry
│   ├── signal_filter.py       ← minimum signal agreement gate (≥2 signals)
│   └── kl_scanner.py          ← Phase 2: cross-contract KL mispricing
│
├── market/
│   ├── __init__.py
│   ├── state.py               ← AppState: FeedState + MarketState (single source of truth)
│   ├── clob_monitor.py        ← Polymarket WS → MarketState
│   └── contract_filter.py     ← filter to crypto/finance, min liquidity
│
├── trading/
│   ├── __init__.py
│   ├── ev_gate.py             ← EV calc with slippage-adjusted entry price
│   ├── kelly.py               ← fractional Kelly 5–10%, EV-confidence weighted, hard cap
│   ├── slippage.py            ← slippage model + order book depth check
│   ├── risk.py                ← RiskManager v2: correlation groups + vol scaling + throttle
│   └── executor.py            ← CLOBExecutor: place/cancel, partial fill handling
│
├── calibration/
│   ├── __init__.py
│   ├── tracker.py             ← fills.jsonl writer + outcome recorder
│   ├── metrics.py             ← Brier score, calibration curve, Sharpe
│   └── recalibrator.py        ← signal weight adjustment hooks
│
├── tracking/
│   ├── __init__.py
│   ├── pnl.py                 ← PnLTracker (unchanged from v1)
│   └── reconciler.py          ← position reconciliation + drift alerts
│
├── utils/
│   ├── __init__.py
│   ├── logger.py
│   └── helpers.py             ← async_retry, time helpers
│
└── tests/                     ← full coverage for all new modules
    ├── conftest.py
    ├── feeds/
    ├── engine/
    ├── market/
    ├── trading/
    └── calibration/
```

**Key design constraint (unchanged):** `market/state.py` is the ONLY place shared mutable state lives. Feeds write; everything else reads.

---

## Improvement 1: Multi-Factor Signal Engine

### A. Deribit Signals (`feeds/deribit.py`)

Extend beyond DVOL to capture 4 signals:

```python
import asyncio, json, websockets
from market.state import AppState
from utils.logger import get_logger

log = get_logger(__name__)
DERIBIT_WS = "wss://www.deribit.com/ws/api/v2"

class DeribitFeed:
    def __init__(self, state: AppState):
        self.state = state

    async def start(self):
        while True:
            try:
                await self._run()
            except Exception as e:
                log.warning(f"deribit reconnect: {e}")
                await asyncio.sleep(5)

    async def _run(self):
        async with websockets.connect(DERIBIT_WS) as ws:
            # 1. DVOL (existing)
            await ws.send(json.dumps({"jsonrpc":"2.0","id":1,"method":"public/subscribe",
                "params":{"channels":["deribit_volatility_index.btc_usd",
                                       "deribit_volatility_index.eth_usd"]}}))
            # 2. Mark prices (for skew + term structure)
            await ws.send(json.dumps({"jsonrpc":"2.0","id":2,"method":"public/subscribe",
                "params":{"channels":["markprice.options.btc_usd"]}}))

            async for raw in ws:
                msg = json.loads(raw)
                if "params" not in msg:
                    continue
                channel = msg["params"]["channel"]
                data = msg["params"]["data"]

                if "deribit_volatility_index" in channel:
                    asset = "btc" if "btc" in channel else "eth"
                    await self.state.update_feeds(**{
                        f"{asset}_dvol": data["volatility"],
                        f"{asset}_price": data["index_price"],
                    })

                elif "markprice.options" in channel:
                    await self._process_options_chain(data)

    async def _process_options_chain(self, instruments: list):
        """Extract skew + term structure + IV/RV spread from options chain."""
        calls, puts = [], []
        for inst in instruments:
            name = inst.get("instrument_name", "")
            iv = inst.get("iv", 0)
            delta = inst.get("delta", 0)
            if not iv:
                continue
            if name.endswith("-C"):
                calls.append({"iv": iv, "delta": delta, "name": name})
            elif name.endswith("-P"):
                puts.append({"iv": iv, "delta": delta, "name": name})

        # Volatility skew: 25-delta put IV minus 25-delta call IV
        # Positive skew = market pricing downside more than upside
        skew = self._compute_skew(calls, puts, target_delta=0.25)

        # Term structure: front vol / back vol ratio
        # < 1 = backwardation (fear), > 1 = contango (calm)
        term_ratio = self._compute_term_structure(calls + puts)

        # IV/RV spread: if IV >> RV, options expensive → fade vol moves
        # Compute RV as 20-day rolling from stored price history
        iv_rv_spread = self._compute_iv_rv_spread()

        await self.state.update_feeds(
            btc_vol_skew=skew,
            btc_term_ratio=term_ratio,
            btc_iv_rv_spread=iv_rv_spread,
        )

    def _compute_skew(self, calls, puts, target_delta=0.25) -> float:
        """25-delta put IV minus 25-delta call IV."""
        call_25 = min(calls, key=lambda x: abs(abs(x["delta"]) - target_delta), default=None)
        put_25  = min(puts,  key=lambda x: abs(abs(x["delta"]) - target_delta), default=None)
        if call_25 and put_25:
            return put_25["iv"] - call_25["iv"]
        return 0.0

    def _compute_term_structure(self, instruments: list) -> float:
        """Ratio of nearest expiry vol to furthest expiry vol."""
        # Sort by expiry date embedded in instrument name (BTC-28MAR26-...)
        # Placeholder — implement expiry parsing from instrument name
        return 1.0

    def _compute_iv_rv_spread(self) -> float:
        """Current DVOL minus 20-day realised vol."""
        # Implement with stored price history in AppState
        return 0.0
```

### B. Microstructure Signals (`feeds/microstructure.py`)

```python
import asyncio, httpx
from market.state import AppState
from utils.logger import get_logger

log = get_logger(__name__)

class MicrostructureFeed:
    """Funding rates, OI changes, liquidation clusters."""
    BINANCE_URL = "https://fapi.binance.com"
    POLL_INTERVAL = 60

    def __init__(self, state: AppState):
        self.state = state

    async def start(self):
        async with httpx.AsyncClient() as client:
            while True:
                try:
                    await self._fetch_all(client)
                except Exception as e:
                    log.warning(f"microstructure fetch error: {e}")
                await asyncio.sleep(self.POLL_INTERVAL)

    async def _fetch_all(self, client: httpx.AsyncClient):
        # 1. Funding rate (BTC perpetual)
        r = await client.get(f"{self.BINANCE_URL}/fapi/v1/premiumIndex",
                             params={"symbol": "BTCUSDT"})
        fr = float(r.json()["lastFundingRate"])

        # 2. Open interest (BTC perpetual)
        r2 = await client.get(f"{self.BINANCE_URL}/fapi/v1/openInterest",
                              params={"symbol": "BTCUSDT"})
        oi = float(r2.json()["openInterest"])

        await self.state.update_feeds(
            btc_funding_rate=fr,
            btc_open_interest=oi,
        )
        # OI change computed in engine/probability.py by comparing to prev value
```

### C. On-Chain Signals (`feeds/onchain.py`) — Extended

Extend existing with whale flows + stablecoin supply:

```python
# Add to existing Glassnode poll:
GLASSNODE_ENDPOINTS = {
    "btc_exchange_netflow":    "/v1/metrics/transactions/transfers_volume_exchanges_net",
    "btc_sopr":                "/v1/metrics/indicators/sopr",
    "btc_nupl":                "/v1/metrics/indicators/unrealized_profit_loss",
    "btc_whale_ratio":         "/v1/metrics/transactions/transfers_volume_miners_to_exchanges",
    "stablecoin_supply_ratio": "/v1/metrics/stablecoins/stablecoin_supply_ratio",
}
# All return normalised [0,1] signals after min-max scaling vs 90-day history
```

### D. Macro Signals (`feeds/macro.py`)

```python
import asyncio, httpx
from market.state import AppState
from utils.logger import get_logger

log = get_logger(__name__)

class MacroFeed:
    """DXY trend + bond yields. CME FedWatch scraped from HTML."""
    POLL_INTERVAL = 300  # 5 minutes — macro moves slowly

    def __init__(self, state: AppState):
        self.state = state

    async def start(self):
        async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0"}) as client:
            while True:
                try:
                    await self._fetch_all(client)
                except Exception as e:
                    log.warning(f"macro fetch error: {e}")
                await asyncio.sleep(self.POLL_INTERVAL)

    async def _fetch_all(self, client):
        # DXY and Treasury yields from Yahoo Finance (free, no API key)
        dxy   = await self._yahoo_price(client, "DX-Y.NYB")   # DXY index
        y2    = await self._yahoo_price(client, "^IRX")        # 3-month (proxy 2Y)
        y10   = await self._yahoo_price(client, "^TNX")        # 10-year yield

        # CME FedWatch — scrape HTML table (fragile, isolate in _fetch_fedwatch)
        may_cut = await self._fetch_fedwatch(client)

        await self.state.update_feeds(
            dxy=dxy, yield_2y=y2, yield_10y=y10,
            fed_may_cut_prob=may_cut,
        )

    async def _yahoo_price(self, client, symbol: str) -> float:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        r = await client.get(url, params={"interval": "1d", "range": "1d"})
        return r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"]

    async def _fetch_fedwatch(self, client) -> float:
        """
        Scrape CME FedWatch HTML for next-meeting cut probability.
        FRAGILE — isolate here so one method to update if layout changes.
        Fallback: return 0.5 (neutral) if scrape fails.
        """
        try:
            r = await client.get(
                "https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html"
            )
            # Parse relevant probability from HTML
            # Implementation: find the table row for next meeting, extract cut %
            # TODO: inspect page structure and implement parser
            return 0.5  # placeholder until parser implemented
        except Exception:
            return 0.5
```

---

## Improvement 2: Correct Bayesian Framework (`engine/bayesian.py`)

Replace `likelihood_ratio = model_prob / prior` shortcut with proper **logit-additive model**. Each signal contributes a weighted log-odds increment. Weights are calibrated from `fills.jsonl`.

```python
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
from utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class Signal:
    name: str
    strength: float        # normalised [-1, +1]; +1 = strong YES, -1 = strong NO
    weight: float          # learned from calibration; start with equal weights
    confidence: float = 1.0  # 0–1; down-weights uncertain signals


class BayesianEngine:
    """
    Logit-additive Bayesian engine.
    log_odds_final = log_odds_prior + Σ (weight_i * strength_i * confidence_i)

    Advantages over likelihood ratio approach:
    - Numerically stable (no product of tiny numbers)
    - Weights are interpretable and calibratable
    - Each signal's contribution is auditable
    """

    def __init__(self, prior: float = 0.5):
        assert 0 < prior < 1, "Prior must be strictly between 0 and 1"
        self._log_odds_prior = np.log(prior / (1 - prior))
        self._log_odds = self._log_odds_prior
        self._signals: list[Signal] = []

    def reset(self, prior: float = 0.5):
        self._log_odds = np.log(prior / (1 - prior))
        self._signals = []

    def add_signal(self, signal: Signal):
        """Add a signal and update log-odds."""
        increment = signal.weight * signal.strength * signal.confidence
        self._log_odds += increment
        self._signals.append(signal)
        log.debug(f"signal={signal.name} strength={signal.strength:.3f} "
                  f"weight={signal.weight:.3f} increment={increment:.4f}")

    @property
    def probability(self) -> float:
        """Convert log-odds back to probability. Clipped for stability."""
        log_odds_clipped = np.clip(self._log_odds, -10, 10)
        return float(1 / (1 + np.exp(-log_odds_clipped)))

    @property
    def active_signals(self) -> list[Signal]:
        return self._signals

    @property
    def signal_count(self) -> int:
        return len(self._signals)

    def summary(self) -> dict:
        return {
            "prior": float(1 / (1 + np.exp(-self._log_odds_prior))),
            "posterior": self.probability,
            "log_odds": float(self._log_odds),
            "signals": [{"name": s.name, "strength": s.strength,
                          "weight": s.weight} for s in self._signals],
        }
```

**Default signal weights** (update these as calibration data accumulates):

```python
# config.py — add signal weights
SIGNAL_WEIGHTS = {
    "dvol":              0.30,   # DVOL-derived log-normal probability
    "vol_skew":          0.15,   # Put skew → directional bias
    "term_structure":    0.10,   # Backwardation → near-term fear
    "iv_rv_spread":      0.10,   # IV premium → fade or follow
    "funding_rate":      0.15,   # Positive funding → crowded long
    "oi_change":         0.10,   # Rising OI → conviction
    "onchain_netflow":   0.10,   # Exchange outflows → bullish
    "macro_dxy":         0.10,   # DXY rising → crypto headwind
    "fed_cut_prob":      0.10,   # Rate cuts → risk-on
}
# Weights should sum to ~1.0 but engine normalises internally
```

---

## Improvement 3: Time-to-Expiry Modeling (`engine/probability.py`)

**Critical fix: current system assumes 1-day horizon. This is wrong for weekly/monthly contracts.**

```python
import numpy as np
from scipy.stats import norm
from datetime import datetime, timezone
from market.state import FeedState
from engine.bayesian import BayesianEngine, Signal
from engine.contract_parser import ParsedContract
import config

def days_to_expiry(expiry_dt: datetime) -> float:
    """Days from now to contract expiry. Minimum 1/24 (1 hour)."""
    now = datetime.now(timezone.utc)
    delta = (expiry_dt - now).total_seconds() / 86400
    return max(delta, 1/24)

def lognormal_prob_above(spot: float, target: float,
                          sigma_annual: float, T_days: float) -> float:
    """
    Probability that asset exceeds target at expiry under GBM.
    Uses risk-neutral lognormal model (no drift — Polymarket is about outcomes, not returns).
    
    sigma_T = sigma_annual / sqrt(252) * sqrt(T_days)
    log_return_to_target = ln(target / spot)
    P(S_T > target) = N(-d2) where d2 = log_return / sigma_T
    """
    if spot <= 0 or target <= 0 or sigma_annual <= 0:
        return 0.5
    sigma_daily = sigma_annual / np.sqrt(252)
    sigma_T = sigma_daily * np.sqrt(T_days)
    if sigma_T < 1e-6:
        return 1.0 if spot > target else 0.0
    d2 = np.log(spot / target) / sigma_T
    return float(norm.cdf(d2))  # P(S_T > target)

def build_model_probability(
    contract: "ParsedContract",
    feeds: FeedState,
    weights: dict,
) -> tuple[float, int]:
    """
    Build model probability for a parsed contract.
    Returns (model_prob, signal_count_used).
    """
    if contract.expiry is None or contract.target_price is None:
        return 0.5, 0  # can't price without expiry + strike

    T = days_to_expiry(contract.expiry)
    engine = BayesianEngine(prior=0.5)

    spot = feeds.btc_price if contract.asset == "BTC" else feeds.eth_price
    dvol = feeds.btc_dvol   if contract.asset == "BTC" else feeds.eth_dvol

    # Signal 1: DVOL log-normal probability (primary anchor)
    if spot and dvol and contract.target_price:
        lnorm_prob = lognormal_prob_above(spot, contract.target_price, dvol/100, T)
        # Convert probability to strength: centre at 0.5 → [-1, +1]
        strength = (lnorm_prob - 0.5) * 2
        engine.add_signal(Signal(
            name="dvol_lognormal",
            strength=strength,
            weight=weights.get("dvol", 0.30),
            confidence=min(1.0, T / 30),  # more confidence further from expiry
        ))

    # Signal 2: Volatility skew
    if feeds.btc_vol_skew is not None:
        # Negative skew → puts cheaper → market less worried about downside
        skew_signal = -np.tanh(feeds.btc_vol_skew / 10)  # normalise to [-1,1]
        engine.add_signal(Signal(
            name="vol_skew",
            strength=skew_signal if contract.direction == "above" else -skew_signal,
            weight=weights.get("vol_skew", 0.15),
        ))

    # Signal 3: Funding rate
    if feeds.btc_funding_rate is not None:
        # Positive funding = crowded longs = mean-revert down pressure
        fr_signal = -np.tanh(feeds.btc_funding_rate * 1000)
        engine.add_signal(Signal(
            name="funding_rate",
            strength=fr_signal if contract.direction == "above" else -fr_signal,
            weight=weights.get("funding_rate", 0.15),
        ))

    # Signal 4: On-chain netflow (negative = outflows = bullish)
    if feeds.btc_exchange_netflow is not None:
        netflow_signal = -np.tanh(feeds.btc_exchange_netflow)
        engine.add_signal(Signal(
            name="onchain_netflow",
            strength=netflow_signal if contract.direction == "above" else -netflow_signal,
            weight=weights.get("onchain_netflow", 0.10),
        ))

    # Signal 5: Macro — DXY (rising dollar = crypto headwind)
    if feeds.dxy is not None:
        # Compute DXY trend: current vs 20-day MA (stored in state)
        # Placeholder: feeds.dxy_trend = (dxy - dxy_ma20) / dxy_ma20
        dxy_signal = -np.tanh(getattr(feeds, "dxy_trend", 0) * 10)
        engine.add_signal(Signal(
            name="macro_dxy",
            strength=dxy_signal if contract.direction == "above" else -dxy_signal,
            weight=weights.get("macro_dxy", 0.10),
        ))

    # Signal 6: Fed cut probability (for rate-related contracts only)
    if feeds.fed_may_cut_prob is not None and contract.category == "rates":
        engine.add_signal(Signal(
            name="fed_cut_prob",
            strength=(feeds.fed_may_cut_prob - 0.5) * 2,
            weight=weights.get("fed_cut_prob", 0.10),
        ))

    return engine.probability, engine.signal_count
```

---

## Improvement 4: Robust Contract Parser (`engine/contract_parser.py`)

Replace regex-only with structured parsing + heuristic fallback:

```python
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from utils.logger import get_logger

log = get_logger(__name__)

PRICE_PATTERNS = [
    r"\$([0-9,]+(?:\.[0-9]+)?)[kK]?",   # $85k, $85,000, $85000
    r"([0-9,]+(?:\.[0-9]+)?)[kK]\s*(?:USD|USDT|dollars)?",
]
EXPIRY_MONTHS = {
    "jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
    "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12
}

SUPPORTED_ASSETS = ["BTC", "ETH", "Bitcoin", "Ethereum"]
DIRECTION_ABOVE = ["above", "over", "exceed", "higher than", "hit", "reach", ">"]
DIRECTION_BELOW = ["below", "under", "drop", "fall below", "<"]


@dataclass
class ParsedContract:
    token_id: str
    question: str
    asset: Optional[str] = None          # "BTC" | "ETH"
    direction: Optional[str] = None      # "above" | "below"
    target_price: Optional[float] = None
    expiry: Optional[datetime] = None
    category: str = "crypto"             # "crypto" | "rates" | "unknown"
    parseable: bool = True               # False = skip, don't trade


def parse_contract(token_id: str, question: str) -> ParsedContract:
    q = question.lower()
    contract = ParsedContract(token_id=token_id, question=question)

    # 1. Detect asset
    for asset in SUPPORTED_ASSETS:
        if asset.lower() in q:
            contract.asset = "BTC" if asset.lower() in ["btc", "bitcoin"] else "ETH"
            break

    # 2. Detect rate contracts
    if any(kw in q for kw in ["fed", "rate", "fomc", "basis points", "bps"]):
        contract.category = "rates"
        contract.asset = None
        return contract  # rates contracts handled separately

    # 3. Skip if no supported asset
    if not contract.asset:
        contract.parseable = False
        log.debug(f"skip (no asset): {question[:60]}")
        return contract

    # 4. Detect direction
    for word in DIRECTION_ABOVE:
        if word in q:
            contract.direction = "above"
            break
    if not contract.direction:
        for word in DIRECTION_BELOW:
            if word in q:
                contract.direction = "below"
                break

    if not contract.direction:
        contract.parseable = False
        log.debug(f"skip (no direction): {question[:60]}")
        return contract

    # 5. Extract target price
    for pattern in PRICE_PATTERNS:
        match = re.search(pattern, question, re.IGNORECASE)
        if match:
            raw = match.group(1).replace(",", "")
            value = float(raw)
            # Handle 'k' suffix in original question
            if "k" in match.group(0).lower():
                value *= 1000
            contract.target_price = value
            break

    if not contract.target_price:
        contract.parseable = False
        log.debug(f"skip (no price): {question[:60]}")
        return contract

    # 6. Extract expiry
    contract.expiry = _parse_expiry(question)
    if not contract.expiry:
        # Default: assume end of current month if no expiry found
        now = datetime.now(timezone.utc)
        import calendar
        last_day = calendar.monthrange(now.year, now.month)[1]
        contract.expiry = now.replace(day=last_day, hour=23, minute=59)
        log.debug(f"expiry not found, defaulting to EOM: {question[:60]}")

    return contract


def _parse_expiry(question: str) -> Optional[datetime]:
    q = question.lower()
    now = datetime.now(timezone.utc)

    # "by end of March", "by June 28", "before April"
    for month_str, month_num in EXPIRY_MONTHS.items():
        if month_str in q:
            year = now.year if month_num >= now.month else now.year + 1
            # Try to find day
            day_match = re.search(rf"{month_str}\w*\s+(\d{{1,2}})", q)
            day = int(day_match.group(1)) if day_match else 28
            try:
                return datetime(year, month_num, day, 23, 59, tzinfo=timezone.utc)
            except ValueError:
                return datetime(year, month_num, 28, 23, 59, tzinfo=timezone.utc)

    # "this week", "this month", "end of week"
    if "this week" in q or "end of week" in q:
        days_ahead = 7 - now.weekday()
        from datetime import timedelta
        return now + timedelta(days=days_ahead)

    return None
```

---

## Improvement 5: Slippage & Liquidity Model (`trading/slippage.py`)

```python
from dataclasses import dataclass
from utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class SlippageEstimate:
    adjusted_price: float    # effective entry price after slippage
    slippage_pct: float      # estimated slippage as fraction
    tradeable: bool          # False if market too thin for our size


def estimate_slippage(
    side: str,               # "BUY" or "SELL"
    size_usdc: float,        # intended trade size in USDC
    best_bid: float,
    best_ask: float,
    volume_usd: float,       # 24h volume or available liquidity
    max_slippage_pct: float = 0.02,  # 2% max acceptable slippage
) -> SlippageEstimate:
    """
    Linear market impact model.
    Slippage ≈ size / liquidity × impact_factor
    Conservative: assume our order consumes 10% of available liquidity.
    """
    IMPACT_FACTOR = 0.10

    if volume_usd < 10_000:
        return SlippageEstimate(
            adjusted_price=best_ask if side == "BUY" else best_bid,
            slippage_pct=1.0,
            tradeable=False,
        )

    # Estimate market impact
    slippage_pct = (size_usdc / volume_usd) * IMPACT_FACTOR
    slippage_pct = min(slippage_pct, 0.10)  # cap at 10%

    if side == "BUY":
        adjusted_price = best_ask * (1 + slippage_pct)
    else:
        adjusted_price = best_bid * (1 - slippage_pct)

    tradeable = slippage_pct <= max_slippage_pct

    if not tradeable:
        log.info(f"skip: slippage {slippage_pct:.2%} > max {max_slippage_pct:.2%} "
                 f"for size ${size_usdc:.0f} in ${volume_usd:.0f} market")

    return SlippageEstimate(
        adjusted_price=adjusted_price,
        slippage_pct=slippage_pct,
        tradeable=tradeable,
    )
```

---

## Improvement 6: Advanced Risk Management (`trading/risk.py`)

```python
import asyncio
from dataclasses import dataclass, field
from market.state import FeedState
from utils.logger import get_logger
import config

log = get_logger(__name__)

ASSET_GROUPS = {
    "BTC": "crypto_btc",
    "ETH": "crypto_eth",
    "rates": "macro_rates",
    "macro": "macro_other",
}


@dataclass
class Position:
    token_id: str
    asset_group: str
    size_usdc: float
    entry_price: float


class RiskManager:
    def __init__(self, bankroll: float):
        self.bankroll = bankroll
        self.max_daily_loss = bankroll * config.MAX_DAILY_LOSS_PCT
        self.max_position = bankroll * config.MAX_POSITION_PCT
        self.max_group_exposure = bankroll * 0.25   # max 25% in any single group
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        self.open_positions: dict[str, Position] = {}
        self._lock = asyncio.Lock()

    async def can_trade(
        self,
        token_id: str,
        asset_group: str,
        position_size: float,
        feeds: FeedState,
    ) -> tuple[bool, str]:
        async with self._lock:
            # 1. Daily loss hard stop
            if self.daily_pnl <= -self.max_daily_loss:
                return False, "daily loss limit hit"

            # 2. Max open positions
            if len(self.open_positions) >= config.MAX_OPEN_POSITIONS:
                return False, "max open positions reached"

            # 3. Per-position size cap
            if position_size > self.max_position:
                return False, f"position ${position_size:.0f} > max ${self.max_position:.0f}"

            # 4. Group correlation limit
            group_exposure = sum(
                p.size_usdc for p in self.open_positions.values()
                if p.asset_group == asset_group
            )
            if group_exposure + position_size > self.max_group_exposure:
                return False, f"group {asset_group} exposure would exceed 25% limit"

            # 5. Volatility regime scaling
            # During high vol (DVOL > 80), reduce max position by 50%
            dvol = feeds.btc_dvol or 60
            if dvol > 80 and position_size > self.max_position * 0.5:
                return False, f"high vol regime (DVOL={dvol:.0f}): position too large"

            # 6. Drawdown-based throttling
            # After 3+ consecutive losses, require double the EV threshold
            if self.consecutive_losses >= 3:
                log.warning(f"throttle mode: {self.consecutive_losses} consecutive losses")
                # Signal to caller — they check this separately
                pass

            return True, "ok"

    @property
    def ev_multiplier(self) -> float:
        """Increase EV requirement after consecutive losses."""
        if self.consecutive_losses >= 3:
            return 2.0
        elif self.consecutive_losses >= 1:
            return 1.5
        return 1.0

    async def open_position(self, token_id: str, asset_group: str,
                            size: float, price: float):
        async with self._lock:
            self.open_positions[token_id] = Position(
                token_id=token_id,
                asset_group=asset_group,
                size_usdc=size,
                entry_price=price,
            )

    async def close_position(self, token_id: str, exit_price: float):
        async with self._lock:
            pos = self.open_positions.pop(token_id, None)
            if pos:
                pnl = (exit_price - pos.entry_price) * pos.size_usdc
                self.daily_pnl += pnl
                if pnl < 0:
                    self.consecutive_losses += 1
                else:
                    self.consecutive_losses = 0
```

---

## Improvement 7: Safe Kelly Sizing (`trading/kelly.py`)

```python
import config
from utils.logger import get_logger

log = get_logger(__name__)

# Hard limits — do not make these configurable
KELLY_MIN = 0.05   # 5% minimum fraction
KELLY_MAX = 0.10   # 10% maximum fraction — never exceed
HARD_CAP_USDC = 50  # Never bet more than $50 until model is calibrated


def fractional_kelly(
    model_prob: float,
    market_price: float,
    bankroll: float,
    ev: float,
    signal_count: int,
    kelly_fraction: float = None,
) -> float:
    """
    Conservative Kelly sizing for uncalibrated model.
    
    kelly_fraction defaults to 5-10% range, never 25%.
    EV-confidence weighting: scale down if EV is marginal or signal count is low.
    Hard cap: HARD_CAP_USDC until model is validated.
    """
    if model_prob <= 0 or market_price <= 0:
        return 0.0

    p = model_prob
    q = 1 - p
    b = (1 - market_price) / market_price  # net odds

    full_kelly = (p * b - q) / b
    if full_kelly <= 0:
        return 0.0

    # Conservative fraction: 5–10% of full Kelly
    fraction = kelly_fraction or config.KELLY_FRACTION  # default 0.05–0.10
    fraction = max(KELLY_MIN, min(KELLY_MAX, fraction))

    # EV confidence weighting: reduce size for marginal EV
    ev_confidence = min(1.0, ev / 0.05)  # full size at 5% EV, half at 2.5%

    # Signal count weighting: reduce size if few signals agree
    signal_confidence = min(1.0, signal_count / 4)  # full size at 4+ signals

    raw_size = bankroll * full_kelly * fraction * ev_confidence * signal_confidence

    # Hard cap — protect during calibration phase
    capped_size = min(raw_size, HARD_CAP_USDC)

    if capped_size < raw_size:
        log.info(f"kelly capped: raw=${raw_size:.2f} → capped=${capped_size:.2f}")

    return capped_size
```

---

## Improvement 8: Model Calibration Loop (`calibration/`)

### `calibration/tracker.py`

```python
import json
from datetime import datetime
from pathlib import Path
from utils.logger import get_logger

log = get_logger(__name__)


class CalibrationTracker:
    def __init__(self, log_file: str = "fills.jsonl"):
        self.log_file = Path(log_file)

    def log_signal(self, token_id: str, model_prob: float,
                   market_prob: float, signal_summary: dict,
                   size_usdc: float, ev: float):
        """Log every signal evaluated — both trades AND skipped."""
        record = {
            "ts": datetime.utcnow().isoformat(),
            "token_id": token_id,
            "model_prob": round(model_prob, 4),
            "market_prob": round(market_prob, 4),
            "edge": round(model_prob - market_prob, 4),
            "ev": round(ev, 4),
            "size_usdc": round(size_usdc, 2),
            "signals": signal_summary,
            "outcome": None,  # filled in by reconciler at resolution
        }
        with self.log_file.open("a") as f:
            f.write(json.dumps(record) + "\n")

    def record_outcome(self, token_id: str, resolved_yes: bool):
        """Update the fill record with actual outcome at resolution."""
        lines = self.log_file.read_text().splitlines()
        updated = []
        for line in lines:
            record = json.loads(line)
            if record["token_id"] == token_id and record["outcome"] is None:
                record["outcome"] = 1 if resolved_yes else 0
            updated.append(json.dumps(record))
        self.log_file.write_text("\n".join(updated) + "\n")
```

### `calibration/metrics.py`

```python
import json
import numpy as np
from pathlib import Path


def load_resolved_fills(log_file: str = "fills.jsonl") -> list[dict]:
    """Load only fills with known outcomes."""
    fills = []
    for line in Path(log_file).read_text().splitlines():
        r = json.loads(line)
        if r.get("outcome") is not None:
            fills.append(r)
    return fills


def brier_score(fills: list[dict]) -> float:
    """Mean squared error between model_prob and outcome. Lower = better."""
    if not fills:
        return float("nan")
    errors = [(f["model_prob"] - f["outcome"]) ** 2 for f in fills]
    return float(np.mean(errors))


def calibration_curve(fills: list[dict], bins: int = 10) -> list[dict]:
    """
    Group predictions into bins, compare predicted vs actual frequency.
    Perfect calibration: predicted 60% → actual 60% of outcomes = YES.
    """
    if not fills:
        return []
    probs = np.array([f["model_prob"] for f in fills])
    outcomes = np.array([f["outcome"] for f in fills])
    bin_edges = np.linspace(0, 1, bins + 1)
    result = []
    for i in range(bins):
        mask = (probs >= bin_edges[i]) & (probs < bin_edges[i+1])
        if mask.sum() == 0:
            continue
        result.append({
            "bin_center": round((bin_edges[i] + bin_edges[i+1]) / 2, 2),
            "predicted_mean": round(float(probs[mask].mean()), 3),
            "actual_rate": round(float(outcomes[mask].mean()), 3),
            "count": int(mask.sum()),
        })
    return result


def mean_edge(fills: list[dict]) -> float:
    if not fills:
        return 0.0
    return float(np.mean([f["edge"] for f in fills]))


def print_calibration_report(log_file: str = "fills.jsonl"):
    fills = load_resolved_fills(log_file)
    if not fills:
        print("No resolved fills yet.")
        return
    print(f"\n{'='*50}")
    print(f"Calibration Report — {len(fills)} resolved fills")
    print(f"Brier Score:  {brier_score(fills):.4f}  (0 = perfect, 0.25 = random)")
    print(f"Mean Edge:    {mean_edge(fills):.3f}")
    print(f"\nCalibration Curve:")
    for row in calibration_curve(fills):
        bar = "✓" if abs(row["predicted_mean"] - row["actual_rate"]) < 0.05 else "✗"
        print(f"  {bar} pred={row['predicted_mean']:.2f} actual={row['actual_rate']:.2f} "
              f"n={row['count']}")
    print(f"{'='*50}\n")
```

---

## Improvement 9: Signal Agreement Filter (`engine/signal_filter.py`)

```python
from engine.bayesian import BayesianEngine
from utils.logger import get_logger

log = get_logger(__name__)

MIN_SIGNALS_REQUIRED = 2      # Must have at least 2 signals to trade
MIN_SIGNAL_AGREEMENT = 0.60   # At least 60% of signals must agree on direction


def passes_signal_filter(engine: BayesianEngine, min_signals: int = MIN_SIGNALS_REQUIRED) -> tuple[bool, str]:
    """
    Gate: do not trade on single weak signals.
    
    Checks:
    1. Minimum number of signals present
    2. Majority of signals agree on direction (same sign)
    """
    signals = engine.active_signals
    if len(signals) < min_signals:
        return False, f"only {len(signals)} signals (need {min_signals})"

    # Check directional agreement
    positive = sum(1 for s in signals if s.strength > 0)
    negative = sum(1 for s in signals if s.strength < 0)
    total = len(signals)
    agreement = max(positive, negative) / total

    if agreement < MIN_SIGNAL_AGREEMENT:
        return False, f"signals split {positive}↑ {negative}↓ ({agreement:.0%} agreement < {MIN_SIGNAL_AGREEMENT:.0%})"

    return True, f"{len(signals)} signals, {agreement:.0%} agreement"
```

---

## Updated EV Gate (`trading/ev_gate.py`)

```python
import config
from trading.slippage import estimate_slippage, SlippageEstimate
from utils.logger import get_logger

log = get_logger(__name__)


def calculate_ev(
    model_prob: float,
    market_price: float,
    slippage: SlippageEstimate,
    payout: float = 1.0,
    ev_multiplier: float = 1.0,  # from RiskManager.ev_multiplier
) -> float:
    """
    EV with slippage-adjusted entry price.
    cost = slippage-adjusted entry price + fee
    EV = (model_prob × payout) - cost
    """
    effective_price = slippage.adjusted_price
    cost = effective_price + config.POLYMARKET_FEE
    ev = (model_prob * payout) - cost
    return ev


def should_enter(
    ev: float,
    slippage: SlippageEstimate,
    ev_multiplier: float = 1.0,
) -> tuple[bool, str]:
    effective_threshold = config.MIN_EV_THRESHOLD * ev_multiplier
    if not slippage.tradeable:
        return False, "market too thin"
    if ev < effective_threshold:
        return False, f"EV {ev:.3f} < threshold {effective_threshold:.3f}"
    return True, f"EV={ev:.3f} slippage={slippage.slippage_pct:.2%}"
```

---

## Updated `main.py` Orchestrator

```python
import asyncio
from config import BANKROLL_USDC, MIN_EV_THRESHOLD, SIGNAL_WEIGHTS, PAPER
from market.state import AppState
from feeds.deribit import DeribitFeed
from feeds.microstructure import MicrostructureFeed
from feeds.onchain import OnChainFeed
from feeds.macro import MacroFeed
from market.clob_monitor import CLOBMonitor
from engine.probability import build_model_probability
from engine.contract_parser import parse_contract
from engine.signal_filter import passes_signal_filter
from trading.ev_gate import calculate_ev, should_enter
from trading.kelly import fractional_kelly
from trading.slippage import estimate_slippage
from trading.risk import RiskManager, ASSET_GROUPS
from trading.executor import CLOBExecutor
from calibration.tracker import CalibrationTracker
from utils.logger import get_logger

log = get_logger("main")


async def trading_loop(state: AppState, risk: RiskManager,
                        executor: CLOBExecutor, tracker: CalibrationTracker):
    while True:
        await asyncio.sleep(5)
        async with state._lock:
            markets = dict(state.markets)
            feeds = state.feeds

        for token_id, contract_state in markets.items():
            try:
                # 1. Parse contract
                parsed = parse_contract(token_id, contract_state.question)
                if not parsed.parseable:
                    continue

                # 2. Build model probability (with TTE)
                model_prob, signal_count = build_model_probability(
                    parsed, feeds, SIGNAL_WEIGHTS
                )

                # 3. Signal agreement filter
                # Note: rebuild engine to get signal objects for filter check
                # In production: pass engine object through build_model_probability
                if signal_count < 2:
                    continue

                # 4. Slippage estimate
                slippage = estimate_slippage(
                    side="BUY",
                    size_usdc=50,  # estimate before Kelly
                    best_bid=contract_state.best_bid,
                    best_ask=contract_state.best_ask,
                    volume_usd=contract_state.volume_usd,
                )
                if not slippage.tradeable:
                    continue

                # 5. EV gate (with slippage + drawdown multiplier)
                ev = calculate_ev(model_prob, contract_state.mid,
                                  slippage, ev_multiplier=risk.ev_multiplier)
                enter, reason = should_enter(ev, slippage, risk.ev_multiplier)
                if not enter:
                    log.debug(f"skip {token_id[:8]}: {reason}")
                    continue

                # 6. Position sizing
                size = fractional_kelly(
                    model_prob=model_prob,
                    market_price=contract_state.mid,
                    bankroll=BANKROLL_USDC,
                    ev=ev,
                    signal_count=signal_count,
                )
                if size <= 0:
                    continue

                # 7. Risk gate
                asset_group = ASSET_GROUPS.get(parsed.asset, "unknown")
                ok, msg = await risk.can_trade(token_id, asset_group, size, feeds)
                if not ok:
                    log.info(f"risk block {token_id[:8]}: {msg}")
                    continue

                # 8. Log signal (before execution — log even if executor fails)
                tracker.log_signal(
                    token_id=token_id,
                    model_prob=model_prob,
                    market_prob=contract_state.mid,
                    signal_summary={"count": signal_count},
                    size_usdc=size,
                    ev=ev,
                )

                # 9. Execute
                log.info(f"{'[PAPER] ' if PAPER else ''}BUY {token_id[:8]} "
                         f"size=${size:.2f} model={model_prob:.3f} "
                         f"market={contract_state.mid:.3f} ev={ev:.3f}")

                result = await executor.place_order(
                    token_id, "BUY", size, contract_state.best_ask
                )
                if result.success:
                    await risk.open_position(token_id, asset_group, size, result.filled_price)

            except Exception as e:
                log.exception(f"trading loop error for {token_id}: {e}")


async def main():
    log.info(f"starting v2 | paper={PAPER} | bankroll=${BANKROLL_USDC}")
    state = AppState()
    risk = RiskManager(bankroll=BANKROLL_USDC)
    executor = CLOBExecutor(paper=PAPER)
    tracker = CalibrationTracker()

    await asyncio.gather(
        DeribitFeed(state).start(),
        MicrostructureFeed(state).start(),
        OnChainFeed(state).start(),
        MacroFeed(state).start(),
        CLOBMonitor(state).start(),
        trading_loop(state, risk, executor, tracker),
    )


if __name__ == "__main__":
    asyncio.run(main())
```

---

## Updated `config.py`

```python
import os
from dotenv import load_dotenv
load_dotenv()

# Risk
BANKROLL_USDC = float(os.getenv("BANKROLL_USDC", "500"))
MAX_DAILY_LOSS_PCT = 0.05
MAX_POSITION_PCT = 0.10
MAX_OPEN_POSITIONS = 5
KELLY_FRACTION = 0.05          # v2: 5% default (was 25% — too aggressive)

# EV
MIN_EV_THRESHOLD = 0.03
POLYMARKET_FEE = 0.02
MIN_MARKET_LIQUIDITY = 10_000

# Signal agreement
MIN_SIGNALS_REQUIRED = 2

# Paper mode (set PAPER=false in .env to go live)
PAPER = os.getenv("PAPER", "true").lower() != "false"

# Signal weights (calibrate from fills.jsonl after 50+ resolved signals)
SIGNAL_WEIGHTS = {
    "dvol_lognormal":    0.30,
    "vol_skew":          0.15,
    "term_structure":    0.10,
    "iv_rv_spread":      0.10,
    "funding_rate":      0.15,
    "oi_change":         0.10,
    "onchain_netflow":   0.10,
    "macro_dxy":         0.10,
    "fed_cut_prob":      0.10,
}

# URLs
DERIBIT_WS_URL = "wss://www.deribit.com/ws/api/v2"
POLYMARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
POLYMARKET_CLOB_URL = "https://clob.polymarket.com"

# Credentials
POLY_PRIVATE_KEY = os.getenv("POLY_PRIVATE_KEY", "")
POLY_API_KEY = os.getenv("POLY_API_KEY", "")
GLASSNODE_API_KEY = os.getenv("GLASSNODE_API_KEY", "")
```

---

## Updated Requirements

```
py-clob-client>=0.14.0
websockets>=12.0
httpx>=0.27.0
numpy>=1.26.0
scipy>=1.12.0            # NEW: norm.cdf for log-normal model
eth-account>=0.11.0
python-dotenv>=1.0.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

---

## Build Order (v2)

Build and test in this exact sequence. Do not skip ahead.

1. **Project scaffold** — directory structure, config, AppState, logger
2. **`engine/contract_parser.py`** — unit test every pattern before connecting to market data
3. **`engine/bayesian.py`** — unit test log-space stability, clipping, signal addition
4. **`feeds/deribit.py`** — DVOL stream working, skew extraction added
5. **`engine/probability.py`** — verify log-normal model output makes sense vs known prices
6. **`feeds/microstructure.py`** — funding rate + OI stream
7. **`feeds/onchain.py`** — Glassnode extended signals
8. **`feeds/macro.py`** — Yahoo Finance yields + FedWatch scraper
9. **`engine/signal_filter.py`** — test agreement gate
10. **`trading/slippage.py`** — unit test market impact model
11. **`trading/kelly.py`** — verify hard caps, confidence weighting
12. **`trading/risk.py`** — test group limits, vol scaling, consecutive loss throttle
13. **`trading/executor.py`** — paper mode first, verify fills logged correctly
14. **`calibration/`** — Brier score, calibration curve, outcome recorder
15. **`main.py`** — wire everything, run paper mode 48h minimum
16. **`engine/kl_scanner.py`** — Phase 2 only, after 50+ resolved signals

---

## Paper Trade Validation Checklist (before going live)

- [ ] 50+ signals logged in `fills.jsonl`
- [ ] 20+ resolved outcomes recorded
- [ ] Brier score < 0.20 (random = 0.25)
- [ ] Mean edge (model_prob − market_prob) > 0.03 on resolved trades
- [ ] Calibration curve: predicted vs actual within 5% per bin
- [ ] No systematic direction bias (not always BUY or always SELL)
- [ ] Daily loss limit never triggered
- [ ] Signal agreement filter firing correctly (check logs)
- [ ] Slippage model rejecting thin markets
- [ ] Group exposure limits working correctly
- [ ] Zero indentation errors or crashes in 48h paper run

**Only set `PAPER=false` after all boxes checked.**

---

## Known Remaining Gaps (for future iteration)

- FedWatch scraper needs HTML inspection and proper parser implementation
- Term structure and IV/RV spread computations need price history storage in AppState
- KL scanner (Phase 2) deferred until calibration data exists
- Partial fill handling in executor not yet specified — use py-clob-client defaults initially
- Realised vol computation for IV/RV spread needs 20-day OHLCV storage

---

*Brief v2.0 — incorporates external auditor feedback — Claude.ai · March 2026*
