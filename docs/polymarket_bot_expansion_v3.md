# Polymarket Bot — v3.0 Expansion Plan: Beyond BTC/ETH

**Date:** March 2026  
**Supersedes:** v2.1 patch  
**Prerequisite:** v2.1 patch must be fully applied and passing tests before starting this plan.  
**Goal:** Expand the bot from BTC/ETH-only to multi-asset crypto, cross-market arbitrage, fixed rate contracts, and macro markets — in that order.

---

## For Claude Code: How to Use This Plan

> **IMPORTANT — Read before implementing:**
>
> 1. Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to work through this plan.
> 2. Each Phase is independent. Complete all steps in a Phase before starting the next.
> 3. **Write tests FIRST** for every change (TDD). Run existing tests after every file change to ensure nothing breaks.
> 4. Steps use checkbox (`- [ ]`) syntax for tracking.
> 5. **Do NOT refactor unrelated code.** Touch only the files listed in each step.
> 6. After each Phase, run the full test suite: `pytest tests/ -v --tb=short`
> 7. If any existing test breaks, STOP and fix the regression before continuing.

---

## Architecture Overview: What Changes and What Doesn't

The current bot has a clean pipeline:

```
Discover → Parse → Signal → Probability → Signal Filter → Slippage → EV Gate → Kelly → Risk → Execute
```

**What stays untouched:** `ev_gate.py`, `kelly.py`, `signal_filter.py`, `slippage.py`, `executor.py`, `bayesian.py`, `calibration/`, `tracking/`, `utils/`. These modules are asset-agnostic — they operate on numbers (model_prob, market_price, EV), not asset types.

**What changes:**

| File | Change Type | Reason |
|------|------------|--------|
| `engine/contract_parser.py` | Extend | Add new assets, rate contract parsing, macro categories |
| `engine/probability.py` | Refactor | Replace hardcoded BTC/ETH lookups with dynamic per-asset dispatch |
| `market/state.py` (`FeedState`) | Refactor | Replace individual asset fields with dictionaries |
| `feeds/microstructure.py` | Extend | Poll funding rates for all supported assets |
| `feeds/onchain.py` | Extend | Add free on-chain data sources (DeFiLlama, Blockchain.com) |
| `feeds/macro.py` | Extend | Add CPI/GDP consensus forecast feeds |
| `market/clob_monitor.py` | Extend | Group markets by asset+expiry for arbitrage scanning |
| `trading/risk.py` | Extend | Update `_contract_group_key()` for new asset categories |
| `config.py` | Extend | New assets, new signal weights, new thresholds |
| `main.py` | Extend | Wire arbitrage scanner as parallel coroutine |
| **NEW** `engine/arb_scanner.py` | Create | Cross-market monotonicity checker |

---

## Phase 1: Multi-Asset Crypto Expansion

**Effort:** 1–2 days  
**Impact:** 4–5× more tradeable markets with zero model changes  
**Risk:** Low — same model, same signals, just more assets

The current bot rejects every non-BTC/ETH market at the parser. Binance has perp funding rates for SOL, XRP, BNB, DOGE, ADA, AVAX — all with deep liquidity. Deribit has DVOL for SOL. The lognormal model in `probability.py` works for any asset with spot price + implied vol.

### Step 1.1: Refactor `FeedState` to Use Dictionaries

**File:** `market/state.py`

The current `FeedState` has hardcoded fields per asset (`btc_price`, `eth_price`, `btc_dvol`, `eth_dvol`, etc.). This won't scale. Replace with dictionaries keyed by asset symbol.

- [ ] **1.1a: Write tests for new FeedState structure**

```python
# tests/market/test_state_v3.py
import pytest
from market.state import FeedState, AppState

def test_feedstate_spot_prices_default_empty():
    fs = FeedState()
    assert fs.spot_prices == {}

def test_feedstate_set_and_get_spot():
    fs = FeedState()
    fs.spot_prices["BTC"] = 85000.0
    fs.spot_prices["SOL"] = 140.0
    assert fs.spot_prices["BTC"] == 85000.0
    assert fs.spot_prices["SOL"] == 140.0

def test_feedstate_dvol_default_empty():
    fs = FeedState()
    assert fs.dvol == {}

def test_feedstate_funding_rates_default_empty():
    fs = FeedState()
    assert fs.funding_rates == {}

def test_feedstate_backward_compat_btc():
    """Ensure old-style access still works via helper properties."""
    fs = FeedState()
    fs.spot_prices["BTC"] = 85000.0
    fs.dvol["BTC"] = 72.0
    assert fs.btc_price == 85000.0
    assert fs.btc_dvol == 72.0

def test_feedstate_backward_compat_eth():
    fs = FeedState()
    fs.spot_prices["ETH"] = 3200.0
    fs.dvol["ETH"] = 65.0
    assert fs.eth_price == 3200.0
    assert fs.eth_dvol == 65.0

def test_feedstate_backward_compat_returns_none_when_missing():
    fs = FeedState()
    assert fs.btc_price is None
    assert fs.eth_dvol is None
```

- [ ] **1.1b: Modify `FeedState` in `market/state.py`**

Replace individual asset fields with dictionaries. Add backward-compatible properties so existing code in `probability.py`, `deribit.py`, etc. doesn't break during the transition.

```python
@dataclass
class FeedState:
    """All feed-derived values. Feeds write here; engine reads here."""
    # === NEW: per-asset dictionaries ===
    spot_prices: dict[str, float] = field(default_factory=dict)      # "BTC" → 85000.0
    dvol: dict[str, float] = field(default_factory=dict)             # "BTC" → 72.0
    funding_rates: dict[str, float] = field(default_factory=dict)    # "BTC" → 0.0001
    vol_skew: dict[str, float] = field(default_factory=dict)         # "BTC" → 2.3

    # === UNCHANGED: macro / on-chain (not per-asset) ===
    btc_exchange_netflow: Optional[float] = None
    dxy_trend: Optional[float] = None
    dxy_confidence: float = 0.0
    yield_10y: Optional[float] = None
    yield_10y_confidence: float = 0.0
    fed_may_cut_prob: Optional[float] = None
    fed_confidence: float = 0.0

    # === Backward-compatible properties (DO NOT REMOVE until all callers migrated) ===
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
```

**CRITICAL:** The backward-compatible properties ensure that `probability.py` (which currently reads `feeds.btc_price`, `feeds.btc_dvol`, etc.) and `deribit.py` (which currently writes `btc_dvol=data["volatility"]`) continue to work UNCHANGED during Phase 1. The old fields are removed in a later cleanup step after all callers are migrated.

- [ ] **1.1c: Update `AppState.update_feeds()` to handle both old-style kwargs and new dict writes**

The `update_feeds` method uses `setattr()` to set fields by name. For the new dict fields, callers will pass kwargs like `spot_BTC=85000.0` (or use a new method). Add a helper:

```python
async def update_asset_feed(self, asset: str, **kwargs):
    """Update per-asset feed data. Example: update_asset_feed("SOL", spot=140.0, funding_rate=0.0002)"""
    async with self._lock:
        if "spot" in kwargs:
            self.feeds.spot_prices[asset] = kwargs["spot"]
        if "dvol" in kwargs:
            self.feeds.dvol[asset] = kwargs["dvol"]
        if "funding_rate" in kwargs:
            self.feeds.funding_rates[asset] = kwargs["funding_rate"]
        if "vol_skew" in kwargs:
            self.feeds.vol_skew[asset] = kwargs["vol_skew"]
```

The existing `update_feeds(**kwargs)` method stays unchanged for backward compat with `deribit.py` and `macro.py`.

- [ ] **1.1d: Run ALL existing tests** — every one must still pass.

```bash
pytest tests/ -v --tb=short
```

---

### Step 1.2: Expand the Contract Parser

**File:** `engine/contract_parser.py`

- [ ] **1.2a: Write tests for new assets**

```python
# tests/engine/test_contract_parser_v3.py
from engine.contract_parser import parse_contract

def test_parse_sol_above():
    c = parse_contract("tok1", "Will SOL be above $200 by end of April?")
    assert c.parseable
    assert c.asset == "SOL"
    assert c.direction == "above"
    assert c.target_price == 200.0

def test_parse_solana_alias():
    c = parse_contract("tok2", "Will Solana exceed $180 by March?")
    assert c.parseable
    assert c.asset == "SOL"

def test_parse_xrp():
    c = parse_contract("tok3", "Will XRP be above $1.50 by June?")
    assert c.parseable
    assert c.asset == "XRP"
    assert c.target_price == 1.50

def test_parse_ripple_alias():
    c = parse_contract("tok4", "Will Ripple reach $2 by May?")
    assert c.parseable
    assert c.asset == "XRP"

def test_parse_bnb():
    c = parse_contract("tok5", "Will BNB be above $700 by April?")
    assert c.parseable
    assert c.asset == "BNB"

def test_parse_doge():
    c = parse_contract("tok6", "Will DOGE hit $0.50 by end of March?")
    assert c.parseable
    assert c.asset == "DOGE"
    assert c.target_price == 0.50

def test_parse_dogecoin_alias():
    c = parse_contract("tok7", "Will Dogecoin be above $0.40 by April?")
    assert c.parseable
    assert c.asset == "DOGE"

def test_parse_ada():
    c = parse_contract("tok8", "Will ADA be above $1 by May?")
    assert c.parseable
    assert c.asset == "ADA"

def test_parse_avax():
    c = parse_contract("tok9", "Will AVAX exceed $50 by June?")
    assert c.parseable
    assert c.asset == "AVAX"

def test_btc_still_works():
    c = parse_contract("tok10", "Will BTC be above $90,000 by end of March?")
    assert c.parseable
    assert c.asset == "BTC"
    assert c.target_price == 90000.0

def test_eth_still_works():
    c = parse_contract("tok11", "Will ETH reach $4,000 by April?")
    assert c.parseable
    assert c.asset == "ETH"
    assert c.target_price == 4000.0
```

- [ ] **1.2b: Update `contract_parser.py`**

Replace the flat `SUPPORTED_ASSETS` list with a mapping of aliases → canonical symbol:

```python
ASSET_ALIASES: dict[str, str] = {
    # BTC
    "btc": "BTC", "bitcoin": "BTC",
    # ETH
    "eth": "ETH", "ethereum": "ETH",
    # SOL
    "sol": "SOL", "solana": "SOL",
    # XRP
    "xrp": "XRP", "ripple": "XRP",
    # BNB
    "bnb": "BNB", "binance coin": "BNB",
    # DOGE
    "doge": "DOGE", "dogecoin": "DOGE",
    # ADA
    "ada": "ADA", "cardano": "ADA",
    # AVAX
    "avax": "AVAX", "avalanche": "AVAX",
}
```

Update the asset detection block in `parse_contract()`:

```python
# Replace the old if/elif block with:
for alias, canonical in ASSET_ALIASES.items():
    if alias in q:
        contract.asset = canonical
        break
```

**IMPORTANT:** Keep the rate keyword detection ABOVE the asset loop (as it already is). Rate contracts should still short-circuit before asset detection.

- [ ] **1.2c: Run ALL tests**

```bash
pytest tests/ -v --tb=short
```

---

### Step 1.3: Update `probability.py` for Multi-Asset

**File:** `engine/probability.py`

Currently has hardcoded:
```python
spot = feeds.btc_price if contract.asset == "BTC" else feeds.eth_price
dvol = feeds.btc_dvol   if contract.asset == "BTC" else feeds.eth_dvol
```

This breaks for any asset that isn't BTC or ETH.

- [ ] **1.3a: Write tests**

```python
# tests/engine/test_probability_v3.py
from unittest.mock import MagicMock
from engine.probability import build_model_probability
from engine.contract_parser import ParsedContract
from market.state import FeedState
from datetime import datetime, timezone, timedelta

def _make_feeds(**kwargs) -> FeedState:
    fs = FeedState()
    for k, v in kwargs.items():
        setattr(fs, k, v)
    return fs

def _make_contract(asset="BTC", target=90000, direction="above") -> ParsedContract:
    return ParsedContract(
        token_id="test",
        question=f"Will {asset} be {direction} ${target}?",
        asset=asset,
        direction=direction,
        target_price=target,
        expiry=datetime.now(timezone.utc) + timedelta(days=7),
        category="crypto",
        parseable=True,
    )

def test_sol_uses_sol_spot_and_dvol():
    fs = FeedState()
    fs.spot_prices["SOL"] = 140.0
    fs.dvol["SOL"] = 80.0
    contract = _make_contract(asset="SOL", target=200)
    prob, count, engine = build_model_probability(contract, fs, {})
    # Should produce a valid probability (not 0.5 fallback)
    assert 0.0 < prob < 1.0
    # With spot 140 and target 200, prob should be < 0.5
    assert prob < 0.5

def test_btc_still_works_after_refactor():
    fs = FeedState()
    fs.spot_prices["BTC"] = 85000.0
    fs.dvol["BTC"] = 72.0
    contract = _make_contract(asset="BTC", target=90000)
    prob, count, engine = build_model_probability(contract, fs, {})
    assert 0.0 < prob < 1.0

def test_missing_spot_returns_neutral():
    fs = FeedState()
    # No spot price for DOGE
    contract = _make_contract(asset="DOGE", target=0.5)
    prob, count, engine = build_model_probability(contract, fs, {})
    assert prob == 0.5

def test_funding_rate_signal_for_sol():
    fs = FeedState()
    fs.spot_prices["SOL"] = 140.0
    fs.dvol["SOL"] = 80.0
    fs.funding_rates["SOL"] = 0.001  # positive = crowded longs
    contract = _make_contract(asset="SOL", target=200)
    prob, count, engine = build_model_probability(
        contract, fs, {"funding_rate": 0.15}
    )
    assert count >= 1  # at least funding rate signal active
```

- [ ] **1.3b: Refactor `build_model_probability()` in `probability.py`**

Replace the hardcoded BTC/ETH lookups with generic per-asset dictionary access:

```python
def build_model_probability(
    contract: ParsedContract,
    feeds: FeedState,
    weights: dict,
) -> tuple[float, int, BayesianEngine]:

    if contract.expiry is None or contract.target_price is None:
        engine = BayesianEngine(prior=0.5)
        return 0.5, 0, engine

    T = days_to_expiry(contract.expiry)
    up = contract.direction == "above"
    asset = contract.asset  # e.g. "BTC", "SOL", "XRP"

    # === CHANGED: generic per-asset lookup ===
    spot = feeds.spot_prices.get(asset)
    asset_dvol = feeds.dvol.get(asset)

    # Lognormal prior
    if spot and asset_dvol and contract.target_price:
        lnorm_prob = lognormal_prob_above(spot, contract.target_price, asset_dvol / 100, T)
        prior = lnorm_prob if up else (1.0 - lnorm_prob)
        prior = float(np.clip(prior, 0.01, 0.99))
    else:
        prior = 0.5

    engine = BayesianEngine(prior=prior)

    # Signal 1: Volatility skew (per-asset if available)
    asset_skew = feeds.vol_skew.get(asset)
    if asset_skew is not None:
        skew_signal = -np.tanh(asset_skew / 10)
        engine.add_signal(Signal(
            name="vol_skew",
            strength=skew_signal if up else -skew_signal,
            weight=weights.get("vol_skew", 0.15),
        ))

    # Signal 2: Funding rate (per-asset)
    asset_funding = feeds.funding_rates.get(asset)
    if asset_funding is not None:
        fr_signal = -np.tanh(asset_funding * 1000)
        engine.add_signal(Signal(
            name="funding_rate",
            strength=fr_signal if up else -fr_signal,
            weight=weights.get("funding_rate", 0.15),
        ))

    # Signal 3: On-chain netflow (BTC-only for now — extend later)
    if feeds.btc_exchange_netflow is not None and asset == "BTC":
        netflow_signal = -np.tanh(feeds.btc_exchange_netflow)
        engine.add_signal(Signal(
            name="onchain_netflow",
            strength=netflow_signal if up else -netflow_signal,
            weight=weights.get("onchain_netflow", 0.10),
        ))

    # Signal 4: DXY trend (applies to ALL crypto — macro headwind)
    if feeds.dxy_trend is not None and feeds.dxy_confidence > 0:
        dxy_signal = -np.tanh(feeds.dxy_trend * 10)
        engine.add_signal(Signal(
            name="macro_dxy",
            strength=dxy_signal if up else -dxy_signal,
            weight=weights.get("macro_dxy", 0.10),
            confidence=feeds.dxy_confidence,
        ))

    # Signal 5: Fed cut probability (rates contracts only)
    if feeds.fed_may_cut_prob is not None and contract.category == "rates":
        engine.add_signal(Signal(
            name="fed_cut_prob",
            strength=(feeds.fed_may_cut_prob - 0.5) * 2,
            weight=weights.get("fed_cut_prob", 0.10),
            confidence=feeds.fed_confidence,
        ))

    return engine.probability, engine.signal_count, engine
```

- [ ] **1.3c: Run ALL tests**

```bash
pytest tests/ -v --tb=short
```

---

### Step 1.4: Expand Microstructure Feed

**File:** `feeds/microstructure.py`

Currently polls Binance for BTCUSDT only. Expand to all supported assets.

- [ ] **1.4a: Write tests**

```python
# tests/feeds/test_microstructure_v3.py
import pytest
from feeds.microstructure import BINANCE_SYMBOLS

def test_binance_symbols_includes_all_assets():
    expected = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT",
                "BNBUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT"]
    for sym in expected:
        assert sym in BINANCE_SYMBOLS, f"Missing {sym}"

def test_binance_symbol_to_asset_mapping():
    from feeds.microstructure import SYMBOL_TO_ASSET
    assert SYMBOL_TO_ASSET["BTCUSDT"] == "BTC"
    assert SYMBOL_TO_ASSET["SOLUSDT"] == "SOL"
    assert SYMBOL_TO_ASSET["DOGEUSDT"] == "DOGE"
```

- [ ] **1.4b: Update `feeds/microstructure.py`**

Add a symbol mapping and iterate over all assets:

```python
BINANCE_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT",
    "BNBUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT",
]
SYMBOL_TO_ASSET = {s: s.replace("USDT", "") for s in BINANCE_SYMBOLS}
# Produces: {"BTCUSDT": "BTC", "ETHUSDT": "ETH", "SOLUSDT": "SOL", ...}

async def _fetch_all(self, client: httpx.AsyncClient):
    for symbol in BINANCE_SYMBOLS:
        asset = SYMBOL_TO_ASSET[symbol]
        try:
            r = await client.get(
                f"{self.BINANCE_URL}/fapi/v1/premiumIndex",
                params={"symbol": symbol},
            )
            fr = float(r.json()["lastFundingRate"])
            await self.state.update_asset_feed(asset, funding_rate=fr)
        except Exception as e:
            log.warning(f"funding rate fetch failed for {symbol}: {e}")
```

**NOTE:** Binance rate-limits at ~1200 requests/min. 8 symbols × 1 request each × every 60s = 8 req/min. Well within limits.

- [ ] **1.4c: Run ALL tests**

```bash
pytest tests/ -v --tb=short
```

---

### Step 1.5: Update Deribit Feed for SOL DVOL

**File:** `feeds/deribit.py`

Deribit offers DVOL for BTC, ETH, and SOL. Other assets (XRP, BNB, DOGE, ADA, AVAX) don't have Deribit DVOL — for those, we fall back to a heuristic IV estimate from Binance implied vol (or use a BTC-beta-scaled vol).

- [ ] **1.5a: Update Deribit subscription to include SOL**

```python
# In _run() — add SOL DVOL channel:
channels = [
    "deribit_volatility_index.btc_usd",
    "deribit_volatility_index.eth_usd",
    "deribit_volatility_index.sol_usd",   # NEW
]
```

- [ ] **1.5b: Update the DVOL message handler to write to dict**

```python
if "deribit_volatility_index" in channel:
    if "btc" in channel:
        asset = "BTC"
    elif "eth" in channel:
        asset = "ETH"
    elif "sol" in channel:
        asset = "SOL"
    else:
        continue
    await self.state.update_asset_feed(
        asset,
        spot=data["index_price"],
        dvol=data["volatility"],
    )
```

- [ ] **1.5c: For assets without Deribit DVOL, add a fallback estimator**

Create a utility function that estimates annualized vol from Binance 20-day price history. Add to `feeds/microstructure.py` or a new `feeds/vol_estimator.py`:

```python
async def estimate_realized_vol(client: httpx.AsyncClient, symbol: str, days: int = 20) -> float:
    """
    Estimate annualized realized volatility from Binance klines.
    Used as DVOL proxy for assets without Deribit options.
    """
    r = await client.get(
        f"https://fapi.binance.com/fapi/v1/klines",
        params={"symbol": symbol, "interval": "1d", "limit": days + 1},
    )
    klines = r.json()
    closes = [float(k[4]) for k in klines]  # close prices
    if len(closes) < 2:
        return 80.0  # fallback to 80% annualized
    log_returns = [np.log(closes[i] / closes[i-1]) for i in range(1, len(closes))]
    daily_vol = np.std(log_returns)
    annualized = daily_vol * np.sqrt(252) * 100  # as percentage
    return max(annualized, 10.0)  # floor at 10%
```

Call this during the microstructure poll for assets that don't have Deribit DVOL, and write the result via `update_asset_feed(asset, dvol=estimated_vol)`.

- [ ] **1.5d: Run ALL tests**

```bash
pytest tests/ -v --tb=short
```

---

### Step 1.6: Update `risk.py` for New Assets

**File:** `trading/risk.py`

The v2.1 patch added `_contract_group_key()` which only handles BTC, ETH, and rates. Extend for new assets.

- [ ] **1.6a: Update `_contract_group_key()`**

```python
def _contract_group_key(self, parsed_contract) -> str:
    asset = parsed_contract.asset
    if asset in ("BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "AVAX"):
        direction = parsed_contract.direction or "above"
        return f"{asset.lower()}_{direction}"
    elif parsed_contract.category == "rates":
        return "macro_rates"
    elif parsed_contract.category == "macro":
        return "macro_econ"
    return "other"
```

- [ ] **1.6b: Run ALL tests**

```bash
pytest tests/ -v --tb=short
```

---

### Step 1.7: Update `config.py`

**File:** `config.py`

- [ ] **1.7a: Add new constants**

```python
# Supported assets for Binance funding rate polling
SUPPORTED_CRYPTO_ASSETS = ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "AVAX"]

# Assets with Deribit DVOL (others use realized vol estimate)
DERIBIT_DVOL_ASSETS = ["BTC", "ETH", "SOL"]

# Per-group max exposure (applies to each direction-bucket)
MAX_GROUP_EXPOSURE_PCT = 0.20  # 20% per direction-bucket (e.g. all SOL-above combined)
```

- [ ] **1.7b: Run ALL tests**

```bash
pytest tests/ -v --tb=short
```

---

### Step 1.8: Integration Test — Full Pipeline with SOL Contract

- [ ] **Write an integration test that sends a SOL contract through the entire pipeline**

```python
# tests/test_integration_multi_asset.py
import asyncio
import pytest
from market.state import AppState, FeedState, ContractState
from engine.contract_parser import parse_contract
from engine.probability import build_model_probability
from engine.signal_filter import passes_signal_filter
from trading.ev_gate import calculate_ev, should_enter, get_trade_direction
from trading.slippage import estimate_slippage

@pytest.mark.asyncio
async def test_sol_contract_full_pipeline():
    """SOL contract flows through the entire pipeline without errors."""
    state = AppState()

    # Seed feeds
    await state.update_asset_feed("SOL", spot=140.0, dvol=85.0, funding_rate=0.0003)
    state.feeds.dxy_trend = -0.02
    state.feeds.dxy_confidence = 0.8

    # 1. Parse
    parsed = parse_contract("sol_tok_1", "Will SOL be above $200 by end of April?")
    assert parsed.parseable
    assert parsed.asset == "SOL"

    # 2. Build probability
    prob, count, engine = build_model_probability(
        parsed, state.feeds, {"funding_rate": 0.15, "macro_dxy": 0.10}
    )
    assert 0.0 < prob < 1.0

    # 3. Signal filter (may or may not pass — just ensure no crash)
    ok, reason = passes_signal_filter(engine, min_signals=1)  # relaxed for test
    # Not asserting ok=True — just that it runs

    # 4. Slippage
    slippage = estimate_slippage(
        side="BUY", size_usdc=50.0,
        best_bid=0.25, best_ask=0.28,
        volume_usd=50000.0,
    )

    # 5. EV gate
    ev, side = calculate_ev(model_prob=prob, market_price=0.26, slippage=slippage)
    # Just verify it returns without error
    assert isinstance(ev, float)
    assert side in ("BUY_YES", "BUY_NO")
```

- [ ] **Run the full test suite**

```bash
pytest tests/ -v --tb=short
```

**Phase 1 is complete when:** All existing tests pass + new multi-asset tests pass + an integration test for a non-BTC/ETH asset passes end-to-end.

---

## Phase 2: Cross-Market Arbitrage Scanner

**Effort:** 2–3 days  
**Impact:** Near-zero risk trades (pure logic arbitrage)  
**Risk:** Very low — no model, no external signals, just internal consistency

### Concept

On Polymarket, there are often multiple BTC price threshold markets for the same expiry:
- "Will BTC be above $80k by end of March?" → YES at $0.75
- "Will BTC be above $85k by end of March?" → YES at $0.50
- "Will BTC be above $90k by end of March?" → YES at $0.30

**Monotonicity rule:** P(BTC > $80k) ≥ P(BTC > $85k) ≥ P(BTC > $90k), always.

If the market ever violates this (e.g. $85k at $0.50 and $90k at $0.50), that's a guaranteed arbitrage: buy YES on the lower strike, buy NO on the higher strike.

### Step 2.1: Create `engine/arb_scanner.py`

- [ ] **2.1a: Write tests**

```python
# tests/engine/test_arb_scanner.py
import pytest
from engine.arb_scanner import (
    ThresholdMarket, find_monotonicity_violations, ArbOpportunity
)

def test_no_violation_when_monotonic():
    markets = [
        ThresholdMarket(token_id="a", asset="BTC", target=80000, direction="above",
                        expiry_key="mar2026", yes_price=0.75, no_token_id="a_no"),
        ThresholdMarket(token_id="b", asset="BTC", target=85000, direction="above",
                        expiry_key="mar2026", yes_price=0.50, no_token_id="b_no"),
        ThresholdMarket(token_id="c", asset="BTC", target=90000, direction="above",
                        expiry_key="mar2026", yes_price=0.30, no_token_id="c_no"),
    ]
    violations = find_monotonicity_violations(markets)
    assert len(violations) == 0

def test_detects_violation():
    markets = [
        ThresholdMarket(token_id="a", asset="BTC", target=85000, direction="above",
                        expiry_key="mar2026", yes_price=0.30, no_token_id="a_no"),
        ThresholdMarket(token_id="b", asset="BTC", target=90000, direction="above",
                        expiry_key="mar2026", yes_price=0.30, no_token_id="b_no"),
    ]
    violations = find_monotonicity_violations(markets)
    assert len(violations) >= 1
    v = violations[0]
    assert v.low_strike_token == "a"   # BTC > $85k (should be more expensive)
    assert v.high_strike_token == "b"  # BTC > $90k

def test_violation_with_inverted_prices():
    """Higher strike priced ABOVE lower strike — clear violation."""
    markets = [
        ThresholdMarket(token_id="a", asset="BTC", target=85000, direction="above",
                        expiry_key="mar2026", yes_price=0.30, no_token_id="a_no"),
        ThresholdMarket(token_id="b", asset="BTC", target=90000, direction="above",
                        expiry_key="mar2026", yes_price=0.40, no_token_id="b_no"),
    ]
    violations = find_monotonicity_violations(markets)
    assert len(violations) >= 1

def test_groups_by_asset_and_expiry():
    """BTC March and BTC April should be separate groups."""
    markets = [
        ThresholdMarket(token_id="a", asset="BTC", target=85000, direction="above",
                        expiry_key="mar2026", yes_price=0.50, no_token_id="a_no"),
        ThresholdMarket(token_id="b", asset="BTC", target=90000, direction="above",
                        expiry_key="apr2026", yes_price=0.60, no_token_id="b_no"),
    ]
    # Different expiries — no violation possible across groups
    violations = find_monotonicity_violations(markets)
    assert len(violations) == 0

def test_minimum_spread_to_be_tradeable():
    """Violation must exceed fees + slippage to be worth trading."""
    markets = [
        ThresholdMarket(token_id="a", asset="BTC", target=85000, direction="above",
                        expiry_key="mar2026", yes_price=0.300, no_token_id="a_no"),
        ThresholdMarket(token_id="b", asset="BTC", target=90000, direction="above",
                        expiry_key="mar2026", yes_price=0.298, no_token_id="b_no"),
    ]
    # 0.2¢ difference — below any reasonable fee threshold
    violations = find_monotonicity_violations(markets, min_spread=0.03)
    assert len(violations) == 0
```

- [ ] **2.1b: Implement `engine/arb_scanner.py`**

```python
"""
engine/arb_scanner.py

Cross-market arbitrage detector.
Scans threshold markets for monotonicity violations.

No external signals needed — pure internal consistency check.
"""

from dataclasses import dataclass
from collections import defaultdict
from utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class ThresholdMarket:
    token_id: str
    asset: str            # "BTC", "SOL", etc.
    target: float         # strike price
    direction: str        # "above" or "below"
    expiry_key: str       # e.g. "mar2026" — used for grouping
    yes_price: float      # current YES price (mid)
    no_token_id: str


@dataclass
class ArbOpportunity:
    low_strike_token: str      # lower strike (should be MORE expensive for "above")
    high_strike_token: str     # higher strike (should be LESS expensive for "above")
    low_strike: float
    high_strike: float
    low_price: float
    high_price: float
    spread: float              # price difference (violation magnitude)
    asset: str
    expiry_key: str
    trade_description: str     # human-readable trade instruction


def find_monotonicity_violations(
    markets: list[ThresholdMarket],
    min_spread: float = 0.03,  # minimum violation to be worth trading (covers fees)
) -> list[ArbOpportunity]:
    """
    Find pairs of same-asset, same-expiry, same-direction markets
    where prices violate monotonicity.

    For "above" direction:
      P(asset > low_strike) >= P(asset > high_strike) must hold.
      If not, buy YES on low_strike + buy NO on high_strike.

    For "below" direction:
      P(asset < low_strike) <= P(asset < high_strike) must hold.
      If not, buy NO on low_strike + buy YES on high_strike.
    """
    # Group by (asset, direction, expiry_key)
    groups: dict[str, list[ThresholdMarket]] = defaultdict(list)
    for m in markets:
        key = f"{m.asset}_{m.direction}_{m.expiry_key}"
        groups[key].append(m)

    violations = []

    for group_key, group_markets in groups.items():
        if len(group_markets) < 2:
            continue

        # Sort by strike price ascending
        sorted_markets = sorted(group_markets, key=lambda m: m.target)

        # Check all pairs for monotonicity violation
        for i in range(len(sorted_markets)):
            for j in range(i + 1, len(sorted_markets)):
                low = sorted_markets[i]   # lower strike
                high = sorted_markets[j]  # higher strike

                if low.direction == "above":
                    # P(above low) should >= P(above high)
                    # Violation: low.yes_price < high.yes_price
                    # OR: they're equal when they shouldn't be (high should be cheaper)
                    violation_spread = high.yes_price - low.yes_price
                    if violation_spread >= -min_spread:
                        # Prices are too close or inverted
                        actual_spread = abs(violation_spread) if violation_spread >= 0 else 0
                        if low.yes_price <= high.yes_price and actual_spread >= min_spread:
                            violations.append(ArbOpportunity(
                                low_strike_token=low.token_id,
                                high_strike_token=high.token_id,
                                low_strike=low.target,
                                high_strike=high.target,
                                low_price=low.yes_price,
                                high_price=high.yes_price,
                                spread=high.yes_price - low.yes_price,
                                asset=low.asset,
                                expiry_key=low.expiry_key,
                                trade_description=(
                                    f"BUY YES {low.asset}>${low.target} @ {low.yes_price:.3f}, "
                                    f"BUY NO {low.asset}>${high.target} @ {1-high.yes_price:.3f}"
                                ),
                            ))
                else:
                    # "below" direction: P(below low) should <= P(below high)
                    # Violation: low.yes_price > high.yes_price
                    if low.yes_price > high.yes_price:
                        spread = low.yes_price - high.yes_price
                        if spread >= min_spread:
                            violations.append(ArbOpportunity(
                                low_strike_token=low.token_id,
                                high_strike_token=high.token_id,
                                low_strike=low.target,
                                high_strike=high.target,
                                low_price=low.yes_price,
                                high_price=high.yes_price,
                                spread=spread,
                                asset=low.asset,
                                expiry_key=low.expiry_key,
                                trade_description=(
                                    f"BUY NO {low.asset}<${low.target} @ {1-low.yes_price:.3f}, "
                                    f"BUY YES {low.asset}<${high.target} @ {high.yes_price:.3f}"
                                ),
                            ))

    # Sort by spread descending (best opportunities first)
    violations.sort(key=lambda v: v.spread, reverse=True)
    return violations
```

- [ ] **2.1c: Run tests**

```bash
pytest tests/engine/test_arb_scanner.py -v
```

---

### Step 2.2: Wire Arb Scanner into `clob_monitor.py`

**File:** `market/clob_monitor.py`

The arb scanner needs grouped market data. Add a method to build `ThresholdMarket` objects from the existing `ContractState` + `ParsedContract` data.

- [ ] **2.2a: Add a helper to build threshold markets from state**

```python
# In clob_monitor.py or a new utility module
from engine.arb_scanner import ThresholdMarket
from engine.contract_parser import parse_contract

def build_threshold_markets(markets: dict[str, ContractState]) -> list[ThresholdMarket]:
    """Convert active ContractState entries into ThresholdMarket objects for arb scanning."""
    result = []
    for yes_id, cs in markets.items():
        parsed = parse_contract(yes_id, cs.question)
        if not parsed.parseable or not parsed.target_price or not parsed.expiry:
            continue
        if parsed.category != "crypto":
            continue
        # Build expiry key for grouping
        expiry_key = parsed.expiry.strftime("%b%Y").lower()  # e.g. "mar2026"
        result.append(ThresholdMarket(
            token_id=yes_id,
            asset=parsed.asset,
            target=parsed.target_price,
            direction=parsed.direction,
            expiry_key=expiry_key,
            yes_price=cs.mid,
            no_token_id=cs.no_token_id,
        ))
    return result
```

---

### Step 2.3: Add Arb Scan to `main.py`

**File:** `main.py`

- [ ] **2.3a: Add a separate arb scanning coroutine**

```python
from engine.arb_scanner import find_monotonicity_violations
from market.clob_monitor import build_threshold_markets  # new helper

_ARB_SCAN_INTERVAL = 30  # seconds — less frequent than main loop

async def arb_scan_loop(
    state: AppState,
    risk: RiskManager,
    executor: CLOBExecutor,
    tracker: CalibrationTracker,
):
    """Scan for cross-market monotonicity violations and execute arb trades."""
    while True:
        await asyncio.sleep(_ARB_SCAN_INTERVAL)
        async with state._lock:
            markets = dict(state.markets)

        threshold_markets = build_threshold_markets(markets)
        violations = find_monotonicity_violations(threshold_markets, min_spread=0.03)

        for v in violations:
            log.info(
                f"ARB DETECTED: {v.trade_description} | spread={v.spread:.3f}"
            )
            # Execute both legs — sizing is the min liquidity of both markets
            # For now, log only. Execution wiring is a separate step.
            tracker.log_signal(
                token_id=f"arb_{v.low_strike_token[:8]}_{v.high_strike_token[:8]}",
                model_prob=0.99,  # arb is near-certain
                market_prob=0.50,
                signal_summary={"type": "arb", "spread": v.spread},
                size_usdc=0.0,  # placeholder until execution wired
                ev=v.spread,
                side="ARB",
            )

        if violations:
            log.info(f"arb scan: {len(violations)} violations found from {len(threshold_markets)} threshold markets")
```

- [ ] **2.3b: Add the arb loop to `asyncio.gather()` in `main()`**

```python
await asyncio.gather(
    DeribitFeed(state).start(),
    MicrostructureFeed(state).start(),
    OnChainFeed(state).start(),
    MacroFeed(state).start(),
    CLOBMonitor(state).start(),
    trading_loop(state, risk, executor, tracker),
    arb_scan_loop(state, risk, executor, tracker),   # NEW
)
```

- [ ] **2.3c: Run ALL tests**

```bash
pytest tests/ -v --tb=short
```

**Phase 2 is complete when:** Arb scanner detects violations in test data + wired into main loop + all existing tests pass.

---

## Phase 3: Fix Broken Rate Contracts

**Effort:** 1–2 days  
**Impact:** Unlocks Fed/FOMC markets (frequently mispriced by retail)  
**Risk:** Low

### Step 3.1: Extend Rate Contract Parsing

**File:** `engine/contract_parser.py`

Currently, rate contracts are detected but return immediately without extracting target/expiry. This means they never get a model probability and are dead branches.

- [ ] **3.1a: Write tests**

```python
# tests/engine/test_rate_parser.py
from engine.contract_parser import parse_contract

def test_fed_cut_25bps():
    c = parse_contract("fed1", "Will the Fed cut rates by 25 basis points in May?")
    assert c.parseable
    assert c.category == "rates"
    assert c.target_price == 25.0  # basis points
    assert c.direction == "below"  # cut = rate goes below
    assert c.expiry is not None

def test_fed_hike_50bps():
    c = parse_contract("fed2", "Will the FOMC hike rates by 50bps in June?")
    assert c.parseable
    assert c.category == "rates"
    assert c.target_price == 50.0
    assert c.direction == "above"  # hike = rate goes above

def test_fed_hold_rates():
    c = parse_contract("fed3", "Will the Fed hold rates steady in May?")
    assert c.parseable
    assert c.category == "rates"
    assert c.direction == "hold"

def test_fed_rate_with_percentage():
    c = parse_contract("fed4", "Will the Fed funds rate be above 4.5% by June?")
    assert c.parseable
    assert c.category == "rates"
    assert c.target_price == 4.5
```

- [ ] **3.1b: Implement rate parsing in `parse_contract()`**

Extend the rate contract branch to extract direction, target, and expiry:

```python
if any(kw in q for kw in RATE_KEYWORDS):
    contract.category = "rates"
    contract.asset = None

    # Direction: cut/lower = below, hike/raise = above, hold = hold
    if any(w in q for w in ["cut", "lower", "reduce", "ease"]):
        contract.direction = "below"
    elif any(w in q for w in ["hike", "raise", "increase", "tighten"]):
        contract.direction = "above"
    elif "hold" in q or "steady" in q or "unchanged" in q:
        contract.direction = "hold"

    # Target: extract bps or percentage
    bps_match = re.search(r"(\d+)\s*(?:basis points|bps|bp)", q)
    if bps_match:
        contract.target_price = float(bps_match.group(1))
    else:
        # Try percentage: "above 4.5%"
        pct_match = re.search(r"(\d+\.?\d*)\s*%", q)
        if pct_match:
            contract.target_price = float(pct_match.group(1))

    # Expiry
    contract.expiry = _parse_expiry(question)
    if not contract.expiry:
        now = datetime.now(timezone.utc)
        last_day = calendar.monthrange(now.year, now.month)[1]
        contract.expiry = now.replace(day=last_day, hour=23, minute=59, second=0, microsecond=0)

    # Mark parseable if we got enough info
    contract.parseable = (contract.direction is not None)
    return contract
```

- [ ] **3.1c: Run ALL tests**

```bash
pytest tests/ -v --tb=short
```

---

### Step 3.2: Wire `fed_may_cut_prob` Properly

**File:** `engine/probability.py`

The signal for rates contracts already exists (`fed_cut_prob`) but never fires because rate contracts never pass parsing. After fixing the parser, ensure the signal actually activates.

- [ ] **3.2a: Write a test**

```python
# tests/engine/test_probability_rates.py
from engine.probability import build_model_probability
from engine.contract_parser import ParsedContract
from market.state import FeedState
from datetime import datetime, timezone, timedelta

def test_rates_contract_uses_fed_signal():
    fs = FeedState()
    fs.fed_may_cut_prob = 0.75
    fs.fed_confidence = 0.9

    contract = ParsedContract(
        token_id="fed_test",
        question="Will the Fed cut rates in May?",
        asset=None,
        direction="below",
        target_price=25.0,
        expiry=datetime.now(timezone.utc) + timedelta(days=30),
        category="rates",
        parseable=True,
    )

    prob, count, engine = build_model_probability(
        contract, fs, {"fed_cut_prob": 0.10}
    )
    assert count >= 1  # fed_cut_prob signal should fire
    # With 75% cut probability, model should lean toward cut
    assert prob != 0.5
```

- [ ] **3.2b: Verify that the existing `probability.py` code handles rate contracts**

The current code already has the `fed_cut_prob` signal gated on `contract.category == "rates"`. With the parser fix, this should now activate. The `prior` for rate contracts will be 0.5 (since there's no spot/dvol for rates), which is correct — the fed signal adjusts from neutral.

No code change needed in `probability.py` for this step — just verify the test passes.

- [ ] **3.2c: Run ALL tests**

```bash
pytest tests/ -v --tb=short
```

**Phase 3 is complete when:** Rate contracts parse correctly, the fed_cut_prob signal fires, and all tests pass.

---

## Phase 4: Free On-Chain Data Sources

**Effort:** 1–2 days  
**Impact:** Replaces the broken Glassnode dependency with working free alternatives  
**Risk:** Low

Glassnode API is paid. Currently `feeds/onchain.py` likely fails silently and `btc_exchange_netflow` stays `None`, meaning Signal 3 in `probability.py` never fires.

### Step 4.1: Integrate DeFiLlama (Stablecoin Flows)

**Free, no API key, no rate limits.**

- [ ] **4.1a: Add DeFiLlama stablecoin supply endpoint**

```python
# In feeds/onchain.py — add alongside or replace Glassnode calls

DEFI_LLAMA_STABLES = "https://stablecoins.llama.fi/stablecoinchains"

async def _fetch_stablecoin_supply(self, client: httpx.AsyncClient) -> Optional[float]:
    """
    Fetch total stablecoin supply on Ethereum + Tron (proxy for crypto buying pressure).
    Rising stablecoin supply = new money entering = bullish signal.
    Returns 30-day change as a normalised signal [-1, +1].
    """
    try:
        r = await client.get(DEFI_LLAMA_STABLES, timeout=15.0)
        data = r.json()
        # Sum stablecoin supply across Ethereum and Tron (two biggest chains)
        # Compare current to 30 days ago
        # Return normalised change
        ...
    except Exception as e:
        log.warning(f"DeFiLlama stablecoin fetch failed: {e}")
        return None
```

### Step 4.2: Integrate Blockchain.com API (BTC Network Stats)

**Free, no API key.**

- [ ] **4.2a: Add Blockchain.com endpoints**

```python
BLOCKCHAIN_COM_BASE = "https://api.blockchain.info"

async def _fetch_btc_mempool(self, client: httpx.AsyncClient) -> Optional[float]:
    """Mempool size — large mempool = network congestion = potential selling pressure."""
    try:
        r = await client.get(f"{BLOCKCHAIN_COM_BASE}/charts/mempool-size?timespan=1days&format=json")
        data = r.json()
        values = data.get("values", [])
        if values:
            return float(values[-1]["y"])
    except Exception as e:
        log.warning(f"Blockchain.com mempool fetch failed: {e}")
    return None

async def _fetch_btc_hash_rate(self, client: httpx.AsyncClient) -> Optional[float]:
    """Hash rate trend — rising hash rate = miner confidence = bullish."""
    try:
        r = await client.get(f"{BLOCKCHAIN_COM_BASE}/charts/hash-rate?timespan=30days&format=json")
        data = r.json()
        values = data.get("values", [])
        if len(values) >= 2:
            current = values[-1]["y"]
            month_ago = values[0]["y"]
            return (current - month_ago) / month_ago  # % change
    except Exception as e:
        log.warning(f"Blockchain.com hashrate fetch failed: {e}")
    return None
```

- [ ] **4.2b: Wire these into `FeedState` and `probability.py` as additional signals**

Add `stablecoin_supply_change` and `btc_hashrate_trend` to `FeedState`. Add corresponding signals in `probability.py` with low weights (0.05 each) since they're slow-moving.

- [ ] **4.2c: Run ALL tests**

```bash
pytest tests/ -v --tb=short
```

---

## Phase 5: Macro Markets (CPI, Unemployment, GDP)

**Effort:** 3–5 days  
**Impact:** High edge potential (retail mispricing is worst here)  
**Risk:** Medium — requires a different modeling approach

### Concept

Macro markets like "Will CPI be above 3.5% in March?" don't need a stochastic price model. The approach is:

1. Get the consensus economic forecast (e.g. CPI consensus = 3.2%)
2. Get the historical forecast error distribution (how often does CPI come in above/below consensus?)
3. Estimate P(CPI > 3.5%) from the error distribution
4. Compare to Polymarket price

### Step 5.1: Add Macro Category to Parser

**File:** `engine/contract_parser.py`

- [ ] **5.1a: Add macro keywords and parsing**

```python
MACRO_KEYWORDS = {
    "cpi": "CPI",
    "inflation": "CPI",
    "consumer price": "CPI",
    "unemployment": "UNEMPLOYMENT",
    "jobless": "UNEMPLOYMENT",
    "nonfarm": "NFP",
    "non-farm": "NFP",
    "payroll": "NFP",
    "gdp": "GDP",
    "gross domestic": "GDP",
}
```

Add macro detection before the crypto asset detection:

```python
# In parse_contract(), after rate detection but before asset detection:
for keyword, macro_type in MACRO_KEYWORDS.items():
    if keyword in q:
        contract.category = "macro"
        contract.asset = macro_type  # reuse asset field for macro type
        # Extract target value (e.g. "3.5%")
        pct_match = re.search(r"(\d+\.?\d*)\s*%", q)
        if pct_match:
            contract.target_price = float(pct_match.group(1))
        # Direction
        for word in DIRECTION_ABOVE:
            if word in q:
                contract.direction = "above"
                break
        if not contract.direction:
            for word in DIRECTION_BELOW:
                if word in q:
                    contract.direction = "below"
                    break
        contract.expiry = _parse_expiry(question)
        if not contract.expiry:
            now = datetime.now(timezone.utc)
            last_day = calendar.monthrange(now.year, now.month)[1]
            contract.expiry = now.replace(day=last_day, hour=23, minute=59, second=0, microsecond=0)
        contract.parseable = (contract.direction is not None and contract.target_price is not None)
        return contract
```

### Step 5.2: Create Macro Probability Model

- [ ] **5.2a: Create `engine/macro_probability.py`**

A separate probability builder for macro contracts. Uses consensus forecasts + historical error distributions instead of lognormal/GBM.

```python
"""
engine/macro_probability.py

Model probability for macro-economic contracts.
Uses consensus forecast + historical forecast error distribution.

Free data sources:
- FRED API (Federal Reserve Economic Data) — free with API key
- Trading Economics scrape — free tier
"""

from scipy.stats import norm
from engine.bayesian import BayesianEngine, Signal
from engine.contract_parser import ParsedContract
from market.state import FeedState
from utils.logger import get_logger

log = get_logger(__name__)

# Historical forecast error std devs (from FRED research papers)
# These represent how much actual values deviate from consensus
FORECAST_ERROR_STD = {
    "CPI": 0.15,          # CPI MoM typically ±0.15% from consensus
    "UNEMPLOYMENT": 0.10,  # unemployment rate ±0.1% from consensus
    "NFP": 50_000,         # nonfarm payrolls ±50k from consensus
    "GDP": 0.30,           # GDP growth ±0.3% from consensus
}


def build_macro_probability(
    contract: ParsedContract,
    feeds: FeedState,
    weights: dict,
) -> tuple[float, int, BayesianEngine]:
    """
    Build probability for macro-economic contracts.
    Model: P(actual > target) = 1 - Φ((target - consensus) / σ_error)
    """
    macro_type = contract.asset  # "CPI", "UNEMPLOYMENT", etc.
    target = contract.target_price

    # Get consensus from feeds (populated by macro.py)
    consensus = getattr(feeds, f"consensus_{macro_type.lower()}", None)
    error_std = FORECAST_ERROR_STD.get(macro_type)

    if consensus is None or error_std is None or target is None:
        engine = BayesianEngine(prior=0.5)
        return 0.5, 0, engine

    # P(actual > target) assuming normal distribution of forecast errors
    z = (target - consensus) / error_std
    prob_above = 1.0 - float(norm.cdf(z))

    if contract.direction == "above":
        prior = prob_above
    else:
        prior = 1.0 - prob_above

    prior = max(0.01, min(0.99, prior))
    engine = BayesianEngine(prior=prior)

    # Add consensus signal
    engine.add_signal(Signal(
        name="macro_consensus",
        strength=(0.5 - abs(prior - 0.5)) * 2,  # stronger when prior is decisive
        weight=weights.get("macro_consensus", 0.20),
    ))

    return engine.probability, engine.signal_count, engine
```

### Step 5.3: Add Consensus Data to Macro Feed

- [ ] **5.3a: Extend `feeds/macro.py` to fetch consensus forecasts**

FRED API is free with an API key (https://fred.stlouisfed.org/docs/api/). Add CPI, unemployment, and GDP consensus data.

```python
# Add to .env.template:
# FRED_API_KEY=your_fred_api_key

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
FRED_SERIES = {
    "CPI": "CPIAUCSL",        # CPI for All Urban Consumers
    "UNEMPLOYMENT": "UNRATE",  # Unemployment Rate
    "GDP": "GDP",              # Gross Domestic Product
}
```

### Step 5.4: Wire Macro Probability into Main Loop

- [ ] **5.4a: Update `main.py` to dispatch to correct probability builder**

```python
# In trading_loop, after parsing:
if parsed.category == "macro":
    from engine.macro_probability import build_macro_probability
    model_prob, signal_count, engine = build_macro_probability(
        parsed, feeds, SIGNAL_WEIGHTS
    )
else:
    model_prob, signal_count, engine = build_model_probability(
        parsed, feeds, SIGNAL_WEIGHTS
    )
```

- [ ] **5.4b: Run ALL tests**

```bash
pytest tests/ -v --tb=short
```

**Phase 5 is complete when:** Macro contracts parse, get probability estimates, and flow through the EV gate. All existing tests pass.

---

## Summary: Build Order and Dependencies

```
Phase 1: Multi-Asset Crypto        (NO dependencies — start here)
    ├── Step 1.1: FeedState refactor
    ├── Step 1.2: Parser expansion
    ├── Step 1.3: Probability refactor
    ├── Step 1.4: Microstructure expansion
    ├── Step 1.5: Deribit SOL + vol estimator
    ├── Step 1.6: Risk grouping
    ├── Step 1.7: Config update
    └── Step 1.8: Integration test

Phase 2: Cross-Market Arbitrage    (depends on Phase 1 parser)
    ├── Step 2.1: arb_scanner.py
    ├── Step 2.2: clob_monitor helper
    └── Step 2.3: main.py wiring

Phase 3: Fix Rate Contracts        (independent — can run parallel with Phase 2)
    ├── Step 3.1: Rate parsing
    └── Step 3.2: Fed signal wiring

Phase 4: Free On-Chain Data        (independent — can run parallel with Phase 2/3)
    ├── Step 4.1: DeFiLlama
    └── Step 4.2: Blockchain.com

Phase 5: Macro Markets             (depends on Phase 1 FeedState + Phase 3 parsing patterns)
    ├── Step 5.1: Macro parser
    ├── Step 5.2: Macro probability model
    ├── Step 5.3: Consensus data feed
    └── Step 5.4: Main loop dispatch
```

---

## Critical Safety Rules

1. **Run `pytest tests/ -v --tb=short` after EVERY file change.** If any existing test breaks, stop and fix before continuing.
2. **Do not refactor files not listed in the plan.** Especially do not touch `bayesian.py`, `kelly.py`, `ev_gate.py`, `slippage.py`, or `executor.py`.
3. **Backward compatibility is mandatory.** The `FeedState` properties (`btc_price`, `eth_price`, etc.) must remain until ALL callers are migrated. Do not remove them.
4. **PAPER=true at all times.** Do not change this. Live trading is NOT part of this plan.
5. **Every new file needs tests.** No exceptions. Write tests FIRST, then implement.
6. **Do not implement Phase 5 until Phases 1-4 are tested and stable.** Phase 5 requires a different modeling approach and is the riskiest change.

---

## What NOT to Do

Do not implement any of these during this plan:
- Signal decorrelation / PCA (needs data from live trading)
- Z-score normalization (needs distribution data)
- Lead-lag orderflow signals (needs latency infrastructure)
- KL scanner integration into trading (Phase 2 from v2 brief — separate initiative)
- Any changes to the executor, Kelly sizing, or risk framework beyond what's listed

---

## Environment Setup Checklist

Before starting, verify:
- [ ] v2.1 patch fully applied and all tests passing
- [ ] Python 3.11+ with venv active
- [ ] All requirements installed: `pip install -r requirements.txt`
- [ ] `.env` has PAPER=true
- [ ] Git clean: `git status` shows no uncommitted changes
- [ ] Commit current state: `git commit -am "checkpoint: pre-v3 expansion"`

---

*v3.0 Expansion Plan — March 2026*
