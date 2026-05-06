# Polymarket Bot — v2.1 Patch (Immediate Fixes)
**Supersedes:** v2.0 partial  
**Scope:** 5 bugs/gaps to fix before first paper run. Do NOT skip these.

---

## Fix 1: Always BUY bug → add SELL / buy NO

Current `main.py` hardcodes `"BUY"` everywhere. You're leaving half the market untradeable.
Overpriced contracts (model_prob < market_price) should be shorted by buying NO shares.

**In `trading/ev_gate.py` — add direction logic:**

```python
def get_trade_direction(model_prob: float, market_price: float) -> tuple[str, float]:
    """
    Returns (side, relevant_price).
    BUY YES when model > market (underpriced YES).
    BUY NO  when model < market (overpriced YES = underpriced NO).
    NO share price = 1 - YES price.
    """
    yes_ev = model_prob - market_price
    no_ev  = (1 - model_prob) - (1 - market_price)  # = market_price - model_prob

    if yes_ev > no_ev:
        return "BUY_YES", market_price   # buy YES token
    else:
        return "BUY_NO", 1 - market_price  # buy NO token

def calculate_ev(model_prob: float, market_price: float,
                 slippage, payout: float = 1.0,
                 ev_multiplier: float = 1.0) -> tuple[float, str]:
    side, entry_price = get_trade_direction(model_prob, market_price)
    effective_prob = model_prob if side == "BUY_YES" else (1 - model_prob)
    effective_price = slippage.adjusted_price  # slippage model already handles side
    cost = effective_price + POLYMARKET_FEE
    ev = (effective_prob * payout) - cost
    return ev, side
```

**In `trading/executor.py` — handle NO token_id:**

```python
async def place_order(self, token_id: str, side: str,
                       size: float, price: float) -> OrderResult:
    """
    side: "BUY_YES" | "BUY_NO"
    For BUY_NO, use the NO token_id (complement of YES token_id).
    Polymarket provides both token IDs per market — store both in ContractState.
    """
    actual_token = token_id if side == "BUY_YES" else self._no_token(token_id)
    # py-clob-client handles the rest identically for YES and NO tokens
    ...
```

**In `market/state.py` — store both token IDs:**

```python
@dataclass
class ContractState:
    yes_token_id: str
    no_token_id: str          # ADD THIS — Polymarket provides both
    question: str
    category: str
    best_bid: float = 0.0     # YES bid
    best_ask: float = 1.0     # YES ask
    volume_usd: float = 0.0

    @property
    def no_best_ask(self) -> float:
        return 1 - self.best_bid  # NO ask = 1 - YES bid
```

---

## Fix 2: Signal filter not wired to engine object

Current `main.py` checks `signal_count < 2` but never calls `passes_signal_filter(engine)`.
The directional agreement check (60% of signals must agree) is completely bypassed.

**In `engine/probability.py` — return engine object:**

```python
def build_model_probability(
    contract: ParsedContract,
    feeds: FeedState,
    weights: dict,
) -> tuple[float, int, "BayesianEngine"]:   # ADD engine to return
    ...
    return engine.probability, engine.signal_count, engine  # ADD engine
```

**In `main.py` — wire the filter properly:**

```python
# Replace:
model_prob, signal_count = build_model_probability(parsed, feeds, SIGNAL_WEIGHTS)
if signal_count < 2:
    continue

# With:
model_prob, signal_count, engine = build_model_probability(parsed, feeds, SIGNAL_WEIGHTS)
ok, reason = passes_signal_filter(engine)
if not ok:
    log.debug(f"signal filter: {reason}")
    continue
```

---

## Fix 3: Asset grouping — BTC 85k and BTC 90k = same exposure

Current grouping treats all BTC contracts as one group, but doesn't handle the case where
two BTC price contracts are highly correlated (both long BTC effectively).
Add a strike-bucketed exposure check.

**In `trading/risk.py` — add contract-level deduplication:**

```python
def _contract_group_key(self, parsed_contract) -> str:
    """
    Group key for exposure tracking.
    BTC contracts bucketed by asset (not by strike) —
    "BTC above $85k" and "BTC above $90k" are both in "btc_long" group
    when direction == "above", "btc_short" when direction == "below".
    """
    if parsed_contract.asset == "BTC":
        direction = parsed_contract.direction or "above"
        return f"btc_{direction}"
    elif parsed_contract.asset == "ETH":
        direction = parsed_contract.direction or "above"
        return f"eth_{direction}"
    elif parsed_contract.category == "rates":
        return "macro_rates"
    return "other"

# Use this key instead of ASSET_GROUPS dict in can_trade()
# Max 25% total exposure per direction bucket
```

**Update `ASSET_GROUPS` in `config.py`:**

```python
# Replace simple dict with dynamic key function — see risk.py above
# Max exposure per direction-bucket (e.g. all BTC-above contracts combined)
MAX_GROUP_EXPOSURE_PCT = 0.25  # 25% of bankroll per direction-bucket
```

---

## Fix 4: EV spread penalty — add adverse selection cost

Current EV misses the cost of crossing the spread and adverse selection.
Real EV is always slightly worse than the naive calculation.

**In `trading/ev_gate.py`:**

```python
POLYMARKET_FEE = 0.02        # taker fee
ADVERSE_SELECTION_PENALTY = 0.005  # 0.5% — latency + adverse selection estimate

def calculate_ev(model_prob: float, market_price: float,
                 slippage, payout: float = 1.0,
                 ev_multiplier: float = 1.0) -> tuple[float, str]:
    side, _ = get_trade_direction(model_prob, market_price)
    effective_prob = model_prob if side == "BUY_YES" else (1 - model_prob)

    # Spread penalty: half the bid-ask spread (cost of crossing)
    spread = slippage.adjusted_price - (
        market_price if side == "BUY_YES" else (1 - market_price)
    )
    spread_penalty = abs(spread) / 2

    cost = (
        slippage.adjusted_price
        + POLYMARKET_FEE
        + spread_penalty
        + ADVERSE_SELECTION_PENALTY
    )
    ev = (effective_prob * payout) - cost
    return ev, side
```

**In `config.py`:**
```python
ADVERSE_SELECTION_PENALTY = 0.005  # tune this down as latency improves
```

---

## Fix 5: Macro feed — caching + fallback + confidence scoring

FedWatch scrape and Yahoo Finance both fail silently. Add three protections:
1. Cache last good value — use it on failure instead of 0.5 placeholder
2. Staleness tracking — reduce signal confidence as data ages
3. Failure counter — log degraded state clearly

**In `feeds/macro.py` — add cache layer:**

```python
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class CachedValue:
    value: float
    fetched_at: datetime
    max_age_seconds: int = 600  # 10 minutes

    @property
    def is_stale(self) -> bool:
        age = (datetime.now(timezone.utc) - self.fetched_at).total_seconds()
        return age > self.max_age_seconds

    @property
    def confidence(self) -> float:
        """Decay from 1.0 to 0.0 as data ages past max_age."""
        age = (datetime.now(timezone.utc) - self.fetched_at).total_seconds()
        return max(0.0, 1.0 - (age / self.max_age_seconds))


class MacroFeed:
    POLL_INTERVAL = 300
    FALLBACK_CONFIDENCE = 0.3  # use cached data at reduced confidence

    def __init__(self, state: AppState):
        self.state = state
        self._cache: dict[str, CachedValue] = {}
        self._fail_count = 0

    async def _fetch_with_cache(self, client, key: str,
                                 fetch_fn, max_age: int = 600) -> tuple[float, float]:
        """
        Returns (value, confidence).
        Tries live fetch; falls back to cache on failure.
        """
        try:
            value = await fetch_fn(client)
            self._cache[key] = CachedValue(
                value=value,
                fetched_at=datetime.now(timezone.utc),
                max_age_seconds=max_age,
            )
            self._fail_count = 0
            return value, 1.0
        except Exception as e:
            self._fail_count += 1
            cached = self._cache.get(key)
            if cached:
                log.warning(f"macro {key} fetch failed (#{self._fail_count}), "
                            f"using cache age={cached.confidence:.2f}: {e}")
                return cached.value, cached.confidence * self.FALLBACK_CONFIDENCE
            else:
                log.error(f"macro {key} fetch failed, no cache: {e}")
                return 0.5, 0.0  # neutral value, zero confidence

    async def _fetch_all(self, client):
        dxy, dxy_conf     = await self._fetch_with_cache(
            client, "dxy", lambda c: self._yahoo_price(c, "DX-Y.NYB"), max_age=300)
        y10, y10_conf     = await self._fetch_with_cache(
            client, "y10", lambda c: self._yahoo_price(c, "^TNX"), max_age=300)
        fed, fed_conf     = await self._fetch_with_cache(
            client, "fed", self._fetch_fedwatch, max_age=3600)

        await self.state.update_feeds(
            dxy=dxy,           dxy_confidence=dxy_conf,
            yield_10y=y10,     yield_10y_confidence=y10_conf,
            fed_may_cut_prob=fed, fed_confidence=fed_conf,
        )
```

**In `market/state.py` — add confidence fields to FeedState:**

```python
@dataclass
class FeedState:
    # ... existing fields ...
    # Macro values
    dxy: Optional[float] = None
    dxy_confidence: float = 0.0       # ADD — 0 = unknown, 1 = fresh
    yield_10y: Optional[float] = None
    yield_10y_confidence: float = 0.0
    fed_may_cut_prob: Optional[float] = None
    fed_confidence: float = 0.0
```

**In `engine/probability.py` — use confidence in Signal construction:**

```python
# When adding macro signals, pass confidence through:
engine.add_signal(Signal(
    name="macro_dxy",
    strength=dxy_signal,
    weight=weights.get("macro_dxy", 0.10),
    confidence=feeds.dxy_confidence,  # 0.0 = ignored, 1.0 = full weight
))
```

Since `BayesianEngine.add_signal` already multiplies `weight × strength × confidence`,
a confidence of 0.0 means the signal contributes zero log-odds — effectively ignored.
No special-casing needed anywhere else.

---

## Summary: What to hand Claude Code

Tell Claude Code:
> "Apply the v2.1 patch. Five fixes in order:
> 1. Add BUY_NO direction to ev_gate.py, executor.py, and ContractState
> 2. Return engine object from build_model_probability, wire passes_signal_filter in main.py
> 3. Replace ASSET_GROUPS dict with _contract_group_key() in risk.py
> 4. Add spread_penalty and ADVERSE_SELECTION_PENALTY to EV calculation
> 5. Add CachedValue + confidence fields to MacroFeed and FeedState
> Write tests for each fix before implementing. Do not touch other files."

---

## What NOT to do yet

Do not implement any of these until you have 50+ resolved paper signals:
- Signal decorrelation / PCA (needs correlation matrix from real data)
- Z-score normalization (needs distribution of signal values from real data)
- Full regime detection (needs regime labels from historical data)
- Lead-lag orderflow signals (needs latency infrastructure first)
- KL scanner (Phase 2 — needs calibrated model first)

These are research problems that require data. Building them before you have fills
will result in arbitrary parameters that feel rigorous but aren't.

---

## The honest strategic picture

The auditor's most important point is the last one:

> "Your system is a well-calibrated consensus model, NOT an information advantage model."

This is true and it's fine — for now. The consensus model will have moderate edge
(the market is not perfectly efficient, especially on shorter-duration contracts).

Your path to elite edge, in order of accessibility:

1. **KL divergence scanner** (Phase 2, already planned) — cross-market mispricing
   on correlated contracts stays open for hours on Polymarket. This is real and buildable.

2. **Disagreement alpha** — trade when your model diverges AND your signals disagree
   internally. This is a sign the market hasn't fully processed new information.

3. **Lead-lag signals** — Binance perpetual orderflow leads spot, which leads Polymarket.
   This requires a low-latency feed and is the hardest to implement correctly.

Build in that order. The KL scanner alone may be sufficient to generate real edge
once your base model is calibrated.

---

*v2.1 patch — March 2026*
