# Unified Regime Score System — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `RegimeDecision` system that reads VPIN, markout, inventory depth, and time-to-resolution to return a single structured object driving spread width, quote size, and fair-value skew — replacing ad-hoc per-signal branching in `QuoteEngine` with one testable decision point.

**Architecture:** New pure-function `maker/regime.py` + new async `maker/vpin_poller.py`; `market/state.py` gains VPIN fields on `ContractState`; `MarkoutTracker` gains per-market/category fallback; `MakerState.skew_factor()` becomes category-aware; `QuoteEngine._reprice()` calls `compute_regime_score()` exactly once per market per cycle.

**Tech Stack:** Python 3.11+, asyncio, httpx (already installed), `config.POLYMARKET_CLOB_URL` for the REST trades endpoint.

---

## File Map

| Action | File | Responsibility |
|---|---|---|
| Create | `maker/regime.py` | `RegimeDecision` dataclass, `compute_regime_score()`, `CATEGORY_SPREAD_MULTIPLIER` |
| Create | `maker/vpin_poller.py` | Async poller — `GET /trades` per market every 30s, writes `cs.vpin` |
| Create | `tests/maker/test_regime.py` | Unit tests for pure scoring function |
| Create | `tests/maker/test_vpin_poller.py` | Unit tests for VPIN computation logic |
| Modify | `market/state.py` | Add `vpin`, `vpin_updated_at`, `hours_to_resolution` property to `ContractState` |
| Modify | `maker/state.py` | Update `skew_factor()` to accept optional `category` for per-category cap |
| Modify | `maker/markout_tracker.py` | Add `by_category` dict + `get_markout_30s()` with 3-tier fallback |
| Modify | `maker/quote_engine.py` | Import + call `compute_regime_score()` once per market; apply all 4 outputs |
| Modify | `maker/runner.py` | Start `VPINPoller` as isolated `asyncio.create_task()` |
| Modify | `tests/maker/test_markout_tracker.py` | Add fallback-chain tests |
| Modify | `tests/maker/test_quote_engine.py` | Add regime-integration behavioral tests |

---

## Task 1: Add VPIN fields and `hours_to_resolution` to `ContractState`

**Files:**
- Modify: `market/state.py`
- Create/Modify: `tests/market/test_state.py`

- [ ] **Step 1.1: Write failing tests**

```python
# tests/market/test_state.py  (create if it doesn't exist)
from datetime import datetime, timezone, timedelta
from market.state import ContractState


def test_contract_state_vpin_defaults():
    cs = ContractState(yes_token_id="a", no_token_id="b", question="q", category="crypto")
    assert cs.vpin == 0.5
    assert cs.vpin_updated_at == 0.0


def test_hours_to_resolution_no_date():
    cs = ContractState(yes_token_id="a", no_token_id="b", question="q", category="crypto")
    assert cs.hours_to_resolution == 48.0


def test_hours_to_resolution_future():
    future = (datetime.now(timezone.utc) + timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cs = ContractState(yes_token_id="a", no_token_id="b", question="q", category="crypto",
                       end_date_iso=future)
    assert 11.5 < cs.hours_to_resolution < 12.5


def test_hours_to_resolution_past_clamps_to_zero():
    past = "2020-01-01T00:00:00Z"
    cs = ContractState(yes_token_id="a", no_token_id="b", question="q", category="crypto",
                       end_date_iso=past)
    assert cs.hours_to_resolution == 0.0
```

- [ ] **Step 1.2: Run tests to confirm failure**

```
pytest tests/market/test_state.py -v
```

Expected: `AttributeError: 'ContractState' object has no attribute 'vpin'`

- [ ] **Step 1.3: Add fields and property to `ContractState` in `market/state.py`**

In `market/state.py`, find the `ContractState` dataclass and add after the `end_date_iso` field, before the first `@property`:

```python
    # Regime score inputs — written by VPINPoller
    vpin: float = 0.5             # size-weighted EMA-smoothed order flow imbalance; 0.5 = neutral
    vpin_updated_at: float = 0.0  # unix timestamp of last VPINPoller write
```

Then add after the existing `no_best_ask` property:

```python
    @property
    def hours_to_resolution(self) -> float:
        """Hours until market resolves. Defaults to 48h if end_date_iso unset."""
        if not self.end_date_iso:
            return 48.0
        from datetime import datetime, timezone
        try:
            end = datetime.fromisoformat(self.end_date_iso.replace("Z", "+00:00"))
            hours = (end - datetime.now(timezone.utc)).total_seconds() / 3600
            return max(0.0, min(168.0, hours))
        except ValueError:
            return 48.0
```

- [ ] **Step 1.4: Run tests to confirm pass**

```
pytest tests/market/test_state.py -v
```

Expected: all 4 tests PASS

- [ ] **Step 1.5: Run full suite to catch regressions**

```
pytest --tb=short -q
```

Expected: all existing tests still pass

- [ ] **Step 1.6: Commit**

```bash
git add market/state.py tests/market/test_state.py
git commit -m "feat(state): add vpin fields and hours_to_resolution property to ContractState"
```

---

## Task 2: Create `maker/regime.py` — pure scoring module

**Files:**
- Create: `maker/regime.py`
- Create: `tests/maker/test_regime.py`

- [ ] **Step 2.1: Write failing tests**

```python
# tests/maker/test_regime.py
import pytest
from maker.regime import RegimeDecision, compute_regime_score, CATEGORY_SPREAD_MULTIPLIER


def _neutral() -> RegimeDecision:
    return compute_regime_score(
        vpin=0.5, markout_30s=0.0, inv_signed=0.0,
        hours_to_resolution=48.0, category="crypto"
    )


def test_neutral_inputs_produce_minimal_regime():
    d = _neutral()
    assert d.score < 0.05
    assert d.spread_multiplier == pytest.approx(1.0, abs=0.05)
    assert d.size_multiplier == pytest.approx(1.0, abs=0.05)
    assert d.skew_adjustment == pytest.approx(0.0, abs=0.001)
    assert d.flags == frozenset()


def test_high_vpin_sets_toxic_flow_flag():
    d = compute_regime_score(vpin=0.85, markout_30s=0.0, inv_signed=0.0,
                             hours_to_resolution=48.0, category="crypto")
    assert "toxic_flow" in d.flags


def test_bad_markout_sets_defensive_flag():
    d = compute_regime_score(vpin=0.5, markout_30s=-0.06, inv_signed=0.0,
                             hours_to_resolution=48.0, category="crypto")
    assert "defensive" in d.flags
    assert d.spread_multiplier > 1.0


def test_unwind_flag_triggers_on_high_inventory_and_short_time():
    d = compute_regime_score(vpin=0.5, markout_30s=0.0, inv_signed=0.7,
                             hours_to_resolution=2.0, category="crypto")
    assert "unwind" in d.flags


def test_unwind_flag_triggers_on_high_inventory_and_bad_markout():
    d = compute_regime_score(vpin=0.5, markout_30s=-0.02, inv_signed=0.7,
                             hours_to_resolution=24.0, category="crypto")
    assert "unwind" in d.flags


def test_extreme_flag_at_high_score():
    d = compute_regime_score(vpin=0.95, markout_30s=-0.10, inv_signed=1.0,
                             hours_to_resolution=1.0, category="crypto")
    assert "extreme" in d.flags


def test_spread_multiplier_bounded():
    import random
    random.seed(42)
    for _ in range(1000):
        d = compute_regime_score(
            vpin=random.random(),
            markout_30s=random.uniform(-0.10, 0.05),
            inv_signed=random.uniform(-1.0, 1.0),
            hours_to_resolution=random.uniform(0, 168),
            category="crypto",
        )
        assert 1.0 <= d.spread_multiplier <= 2.5, f"out of bounds: {d.spread_multiplier}"
        assert 0.25 <= d.size_multiplier <= 1.0, f"out of bounds: {d.size_multiplier}"


def test_skew_opposes_inventory_direction():
    d_long = compute_regime_score(vpin=0.5, markout_30s=0.0, inv_signed=0.8,
                                  hours_to_resolution=48.0, category="crypto")
    d_short = compute_regime_score(vpin=0.5, markout_30s=0.0, inv_signed=-0.8,
                                   hours_to_resolution=48.0, category="crypto")
    assert d_long.skew_adjustment < 0    # long → shade fv down
    assert d_short.skew_adjustment > 0   # short → shade fv up


def test_skew_zero_at_zero_inventory():
    d = compute_regime_score(vpin=0.5, markout_30s=0.0, inv_signed=0.0,
                             hours_to_resolution=48.0, category="crypto")
    assert d.skew_adjustment == 0.0


def test_category_spread_multiplier_table():
    assert CATEGORY_SPREAD_MULTIPLIER["finance"] < CATEGORY_SPREAD_MULTIPLIER["weather"]
    assert CATEGORY_SPREAD_MULTIPLIER["weather"] == 2.0
    assert "default" in CATEGORY_SPREAD_MULTIPLIER
```

- [ ] **Step 2.2: Run tests to confirm failure**

```
pytest tests/maker/test_regime.py -v
```

Expected: `ModuleNotFoundError: No module named 'maker.regime'`

- [ ] **Step 2.3: Create `maker/regime.py`**

```python
"""maker/regime.py — Unified regime score for spread, size, and skew decisions."""

from dataclasses import dataclass
from math import copysign


# ── Category spread baseline ──────────────────────────────────────────────────
# Seeded from Becker (2026) maker-taker gap findings. Tune empirically after
# 500+ fills per category accumulate in fills_markout.jsonl.
CATEGORY_SPREAD_MULTIPLIER: dict[str, float] = {
    "finance":       1.0,
    "crypto":        1.2,
    "politics":      1.3,
    "sports":        1.6,
    "weather":       2.0,
    "entertainment": 2.2,
    "default":       1.5,
}

MIN_SPREAD = 0.005   # 0.5c floor
MAX_SPREAD = 0.15    # 15c ceiling


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


@dataclass(frozen=True)
class RegimeDecision:
    score: float              # 0.0-1.0 composite intensity
    spread_multiplier: float  # 1.0-2.5x dynamic factor (category baseline applied separately)
    size_multiplier: float    # 0.25-1.0x
    skew_adjustment: float    # +/-0-0.02, applied to fair_value BEFORE spread derivation
    flags: frozenset          # {"defensive", "unwind", "extreme", "toxic_flow"}


def compute_regime_score(
    vpin: float,
    markout_30s: float,
    inv_signed: float,
    hours_to_resolution: float,
    category: str,
) -> RegimeDecision:
    """Compute a unified regime decision from four market signals.

    Args:
        vpin: Size-weighted order flow imbalance 0-1. 0.5 = neutral/stale.
        markout_30s: Rolling avg post-fill price movement at T+30s. Negative = adverse.
        inv_signed: Net inventory as signed fraction of per-market cap (-1 to +1).
        hours_to_resolution: Hours until market resolves, clamped [0, 168].
        category: Market category string for flag context.

    Returns:
        RegimeDecision with spread_multiplier, size_multiplier, skew_adjustment, flags.
    """
    hours = max(0.0, hours_to_resolution)

    # Four score components
    vpin_score    = _clamp(abs(vpin - 0.5) / 0.3, 0.0, 1.0)
    markout_score = _clamp(-markout_30s / 0.05, 0.0, 1.0)
    inv_score     = abs(inv_signed)
    time_score    = _clamp((24.0 - hours) / 24.0, 0.0, 1.0) ** 1.5

    score = _clamp(
        0.35 * vpin_score
        + 0.25 * markout_score
        + 0.25 * inv_score
        + 0.15 * time_score,
        0.0, 1.0,
    )

    # Output derivation
    spread_multiplier = 1.0 + score * 1.5          # 1.0x to 2.5x
    size_multiplier   = max(0.25, 1.0 - 0.75 * score)

    # Skew nudges fair_value toward inventory reduction.
    # Small inventory = small skew. High score = stronger push. Cap +/-2c.
    abs_inv = abs(inv_signed)
    if abs_inv == 0.0:
        skew_adjustment = 0.0
    else:
        magnitude = min(0.02, 0.02 * abs_inv * (0.5 + score))
        skew_adjustment = -copysign(magnitude, inv_signed)

    # Flags
    flags: set[str] = set()
    if score > 0.4:
        flags.add("defensive")
    if score > 0.8:
        flags.add("extreme")
    if vpin_score > 0.7:
        flags.add("toxic_flow")
    if (abs_inv > 0.6
            and (markout_30s < -0.01 or hours < 4.0)):
        flags.add("unwind")

    return RegimeDecision(
        score=score,
        spread_multiplier=spread_multiplier,
        size_multiplier=size_multiplier,
        skew_adjustment=skew_adjustment,
        flags=frozenset(flags),
    )
```

- [ ] **Step 2.4: Run tests**

```
pytest tests/maker/test_regime.py -v
```

Expected: all 11 tests PASS

- [ ] **Step 2.5: Commit**

```bash
git add maker/regime.py tests/maker/test_regime.py
git commit -m "feat(regime): add RegimeDecision dataclass and compute_regime_score() pure function"
```

---

## Task 3: Update `MakerState.skew_factor()` to be category-aware

**Files:**
- Modify: `maker/state.py`
- Modify: `tests/maker/test_state.py`

- [ ] **Step 3.1: Write failing test**

Add to `tests/maker/test_state.py`:

```python
import pytest
from maker.state import MakerState


def test_skew_factor_uses_category_cap():
    ms = MakerState()
    ms.category_inventory_caps = {"weather": 10.0}
    ms.max_inventory_per_market = 20.0
    ms.inventory["tok1"] = 8.0

    # With category cap=10, inv=8 -> 0.8
    assert ms.skew_factor("tok1", category="weather") == pytest.approx(0.8)
    # Without category -> uses global cap=20, inv=8 -> 0.4
    assert ms.skew_factor("tok1") == pytest.approx(0.4)
```

- [ ] **Step 3.2: Run to confirm failure**

```
pytest tests/maker/test_state.py::test_skew_factor_uses_category_cap -v
```

Expected: `TypeError: skew_factor() got an unexpected keyword argument 'category'`

- [ ] **Step 3.3: Update `skew_factor()` in `maker/state.py`**

Find `skew_factor` and replace the entire method:

```python
    def skew_factor(self, token_id: str, category: str = "") -> float:
        """Normalized skew: [-1.0, +1.0]. Positive = holding YES.

        Uses per-category cap when category is provided; falls back to
        max_inventory_per_market for unknown or unspecified categories.
        """
        inv = self.get_inventory(token_id)
        cap = (self.max_inventory_for_category(category)
               if category else self.max_inventory_per_market)
        if cap == 0:
            return 0.0
        return max(-1.0, min(1.0, inv / cap))
```

- [ ] **Step 3.4: Run tests**

```
pytest tests/maker/test_state.py -v
pytest tests/maker/ -q
```

Expected: all pass

- [ ] **Step 3.5: Commit**

```bash
git add maker/state.py tests/maker/test_state.py
git commit -m "feat(state): make skew_factor() category-aware for per-cap inventory fraction"
```

---

## Task 4: Extend `MarkoutTracker` with per-category stats and `get_markout_30s()`

**Files:**
- Modify: `maker/markout_tracker.py`
- Modify: `tests/maker/test_markout_tracker.py`

- [ ] **Step 4.1: Write failing tests**

Append to `tests/maker/test_markout_tracker.py`:

```python
from collections import deque as _deque


def _tracker_with_market_fills(n_fills: int, category: str, token_id: str,
                                markout_val: float, tmp_path) -> "MarkoutTracker":
    """Build a MarkoutTracker and inject pre-evaluated T+30s markout samples directly."""
    app = _make_app_state(token_id, bid=0.49, ask=0.51)
    app.markets[token_id].category = category  # patch category
    ms = MakerState()
    q: asyncio.Queue = asyncio.Queue()
    tracker = MarkoutTracker(app, ms, q, log_path=str(tmp_path / "m.jsonl"))

    tracker._market_markouts[token_id] = {i: _deque(maxlen=20) for i in MARKOUT_INTERVALS}
    tracker.by_category.setdefault(category, {i: _deque(maxlen=500) for i in MARKOUT_INTERVALS})
    for _ in range(n_fills):
        tracker._market_markouts[token_id][30].append(markout_val)
        tracker.by_category[category][30].append(markout_val)
        tracker._n_fills += 1
    return tracker


def test_get_markout_30s_returns_per_market_when_sufficient(tmp_path):
    tracker = _tracker_with_market_fills(25, "crypto", "tok-a", -0.01, tmp_path)
    result = tracker.get_markout_30s("tok-a", "crypto")
    assert result == pytest.approx(-0.01, abs=0.001)


def test_get_markout_30s_falls_back_to_category(tmp_path):
    # Per-market only 10 fills (below MIN_MARKET_FILLS=20) — uses category (100+ fills)
    tracker = _tracker_with_market_fills(10, "crypto", "tok-b", -0.02, tmp_path)
    # Top up category to >= MIN_CATEGORY_FILLS
    for _ in range(100):
        tracker.by_category["crypto"][30].append(-0.02)
    result = tracker.get_markout_30s("tok-b", "crypto")
    assert result == pytest.approx(-0.02, abs=0.001)


def test_get_markout_30s_returns_zero_when_no_data(tmp_path):
    app = AppState()
    ms = MakerState()
    q: asyncio.Queue = asyncio.Queue()
    tracker = MarkoutTracker(app, ms, q, log_path=str(tmp_path / "m.jsonl"))
    assert tracker.get_markout_30s("unknown-token", "finance") == 0.0
```

- [ ] **Step 4.2: Run to confirm failure**

```
pytest tests/maker/test_markout_tracker.py -k "get_markout" -v
```

Expected: `AttributeError: 'MarkoutTracker' object has no attribute 'by_category'`

- [ ] **Step 4.3: Update `maker/markout_tracker.py`**

Add after `_ROLLING_WINDOW`:

```python
MIN_MARKET_FILLS = 20     # min per-market fills to trust per-market avg
MIN_CATEGORY_FILLS = 100  # min fills to trust per-category avg
```

In `__init__`, add after `self._markouts`:

```python
        # Per-category rolling markouts: category -> {interval: deque}
        self.by_category: dict[str, dict[int, deque]] = {}
```

In `_evaluate`, after the existing `token_avgs[check.interval] = ...` line, add:

```python
        # Update per-category rolling markouts
        cat = cs.category if cs else ""
        if cat:
            if cat not in self.by_category:
                self.by_category[cat] = {i: deque(maxlen=_ROLLING_WINDOW) for i in MARKOUT_INTERVALS}
            self.by_category[cat][check.interval].append(markout)
```

Add public method after the `stats` property:

```python
    def get_markout_30s(self, token_id: str, category: str) -> float:
        """Rolling avg markout at T+30s with three-tier fallback.

        Returns per-market value if >= MIN_MARKET_FILLS fills exist,
        else per-category value if >= MIN_CATEGORY_FILLS fills exist,
        else 0.0 (neutral).
        """
        per_mkt = self._market_markouts.get(token_id, {}).get(30)
        if per_mkt is not None and len(per_mkt) >= MIN_MARKET_FILLS:
            return sum(per_mkt) / len(per_mkt)
        per_cat = self.by_category.get(category, {}).get(30)
        if per_cat is not None and len(per_cat) >= MIN_CATEGORY_FILLS:
            return sum(per_cat) / len(per_cat)
        return 0.0
```

- [ ] **Step 4.4: Run tests**

```
pytest tests/maker/test_markout_tracker.py -v
```

Expected: all tests pass (existing 5 + new 3)

- [ ] **Step 4.5: Commit**

```bash
git add maker/markout_tracker.py tests/maker/test_markout_tracker.py
git commit -m "feat(markout): add per-category stats and get_markout_30s() 3-tier fallback"
```

---

## Task 5: Create `maker/vpin_poller.py`

**Files:**
- Create: `maker/vpin_poller.py`
- Create: `tests/maker/test_vpin_poller.py`

- [ ] **Step 5.1: Write failing tests**

```python
# tests/maker/test_vpin_poller.py
import time
import pytest
from maker.vpin_poller import VPINPoller, _compute_raw_vpin, _ema


def test_compute_raw_vpin_size_weighted():
    """3 BUY x 10 size, 1 SELL x 5 size -> (30-5)/35 = 0.714"""
    trades = [
        {"side": "BUY",  "size": "10", "price": "0.52"},
        {"side": "BUY",  "size": "10", "price": "0.53"},
        {"side": "BUY",  "size": "10", "price": "0.53"},
        {"side": "SELL", "size": "5",  "price": "0.52"},
    ]
    assert _compute_raw_vpin(trades) == pytest.approx((30 - 5) / 35, abs=0.001)


def test_compute_raw_vpin_below_min_trades():
    """Fewer than MIN_VPIN_TRADES -> neutral 0.5"""
    trades = [{"side": "BUY", "size": "10", "price": "0.50"}] * 5
    assert _compute_raw_vpin(trades) == 0.5


def test_compute_raw_vpin_empty():
    assert _compute_raw_vpin([]) == 0.5


def test_compute_raw_vpin_zero_volume():
    trades = [{"side": "BUY", "size": "0", "price": "0.50"}] * 15
    assert _compute_raw_vpin(trades) == 0.5


def test_ema_smoothing():
    """prev=0.5, raw=0.9 -> 0.7 * 0.5 + 0.3 * 0.9 = 0.62"""
    assert _ema(prev=0.5, raw=0.9) == pytest.approx(0.62, abs=0.001)


def test_vpin_tick_rule_buy_dominant():
    """Consistently rising prices -> high BUY imbalance."""
    trades = [{"price": str(0.50 + i * 0.01), "size": "10"} for i in range(15)]
    raw = _compute_raw_vpin(trades)
    assert raw > 0.5


def test_staleness_resets_to_neutral():
    """get_vpin() returns 0.5 when last update is older than VPIN_STALE_SECONDS."""
    poller = VPINPoller.__new__(VPINPoller)
    poller._vpin_cache = {"tok-a": (0.8, time.time() - 300)}
    assert poller.get_vpin("tok-a") == 0.5


def test_incremental_trades_skip_seen_ids():
    """Trades with IDs already seen are not re-processed."""
    poller = VPINPoller.__new__(VPINPoller)
    poller._last_trade_id = {"tok-a": "id-5"}
    trades = [
        {"id": "id-3", "side": "BUY", "size": "10", "price": "0.5"},
        {"id": "id-5", "side": "BUY", "size": "10", "price": "0.5"},
        {"id": "id-6", "side": "BUY", "size": "10", "price": "0.5"},
    ]
    new_trades = poller._filter_new_trades("tok-a", trades)
    assert len(new_trades) == 1
    assert new_trades[0]["id"] == "id-6"
```

- [ ] **Step 5.2: Run to confirm failure**

```
pytest tests/maker/test_vpin_poller.py -v
```

Expected: `ModuleNotFoundError: No module named 'maker.vpin_poller'`

- [ ] **Step 5.3: Create `maker/vpin_poller.py`**

```python
"""maker/vpin_poller.py — Polls CLOB /trades per market, computes size-weighted VPIN."""

import asyncio
import time
from collections import deque

import httpx

from config import POLYMARKET_CLOB_URL
from market.state import AppState
from utils.logger import get_logger

log = get_logger(__name__)

VPIN_POLL_INTERVAL = 30.0    # seconds between polls per market
VPIN_STALE_SECONDS = 180.0   # reset to neutral if no update within this window
MIN_VPIN_TRADES = 10         # minimum trades in buffer before computing VPIN
VPIN_EMA_ALPHA = 0.3         # weight on new observation; 0.7 on previous smoothed value
_BUFFER_SIZE = 100           # rolling buffer depth per token
_LOG_HEARTBEAT_CYCLES = 50   # log liveness every N poll cycles


def _ema(prev: float, raw: float, alpha: float = VPIN_EMA_ALPHA) -> float:
    """Exponential moving average: (1-alpha)*prev + alpha*raw."""
    return (1.0 - alpha) * prev + alpha * raw


def _compute_raw_vpin(trades: list[dict]) -> float:
    """Compute size-weighted VPIN from a list of classified trade dicts.

    Each dict must have 'size' (str or float) and either:
      - 'side': 'BUY' or 'SELL' (explicit), or
      - 'price' only (tick-rule classification applied).

    Returns 0.5 (neutral) if fewer than MIN_VPIN_TRADES or zero volume.
    """
    if len(trades) < MIN_VPIN_TRADES:
        return 0.5

    classified: list[tuple[str, float]] = []
    prev_price: float | None = None
    last_side = "BUY"

    for t in trades:
        size = float(t.get("size", 0) or 0)
        if "side" in t:
            side = t["side"]
        else:
            price = float(t.get("price", 0) or 0)
            if prev_price is None or price == prev_price:
                side = last_side
            elif price > prev_price:
                side = "BUY"
            else:
                side = "SELL"
            prev_price = price
        last_side = side
        classified.append((side, size))

    buy_vol  = sum(sz for side, sz in classified if side == "BUY")
    sell_vol = sum(sz for side, sz in classified if side == "SELL")
    total_vol = buy_vol + sell_vol

    if total_vol == 0.0:
        return 0.5

    return abs(buy_vol - sell_vol) / total_vol


class VPINPoller:
    """Polls CLOB /trades endpoint per active market and writes smoothed VPIN
    into AppState.markets[token_id].vpin under the existing market lock.

    Runs as an independent asyncio task. Per-token failures are caught and
    isolated — a bad token does not affect other markets or the main loop.
    """

    def __init__(self, app_state: AppState):
        self._app = app_state
        self._buffers: dict[str, deque] = {}
        self._last_trade_id: dict[str, str] = {}
        self._vpin_cache: dict[str, tuple[float, float]] = {}  # token -> (vpin, updated_at)
        self._cycle = 0

    def _filter_new_trades(self, token_id: str, trades: list[dict]) -> list[dict]:
        """Return only trades newer than last_trade_id for this token."""
        last_id = self._last_trade_id.get(token_id)
        if last_id is None:
            return trades
        new = []
        for t in trades:
            if t.get("id") == last_id:
                break
            new.append(t)
        return new

    def get_vpin(self, token_id: str) -> float:
        """Return cached VPIN, resetting to 0.5 if stale."""
        entry = self._vpin_cache.get(token_id)
        if entry is None:
            return 0.5
        vpin, updated_at = entry
        if time.time() - updated_at > VPIN_STALE_SECONDS:
            return 0.5
        return vpin

    async def _poll_token(self, client: httpx.AsyncClient, token_id: str) -> None:
        """Fetch new trades for one token and update VPIN in AppState."""
        try:
            resp = await client.get(
                f"{POLYMARKET_CLOB_URL}/trades",
                params={"token_id": token_id, "limit": 100},
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
            trades_raw: list[dict] = data if isinstance(data, list) else data.get("data", [])
        except Exception as exc:
            log.debug(f"vpin_poller: fetch failed [{token_id[:8]}]: {exc}")
            return

        new_trades = self._filter_new_trades(token_id, trades_raw)
        if new_trades:
            self._last_trade_id[token_id] = new_trades[0].get("id", "")

        buf = self._buffers.setdefault(token_id, deque(maxlen=_BUFFER_SIZE))
        buf.extend(new_trades)

        raw_vpin = _compute_raw_vpin(list(buf))
        prev_vpin = self._vpin_cache.get(token_id, (0.5, 0.0))[0]
        smoothed = _ema(prev=prev_vpin, raw=raw_vpin)

        now = time.time()
        self._vpin_cache[token_id] = (smoothed, now)

        async with self._app._lock:
            cs = self._app.markets.get(token_id)
            if cs is not None:
                cs.vpin = smoothed
                cs.vpin_updated_at = now

    async def run(self) -> None:
        """Main loop — poll all active markets every VPIN_POLL_INTERVAL seconds."""
        async with httpx.AsyncClient() as client:
            while True:
                try:
                    async with self._app._lock:
                        token_ids = list(self._app.markets.keys())

                    for token_id in token_ids:
                        try:
                            await self._poll_token(client, token_id)
                        except Exception as exc:
                            log.debug(f"vpin_poller: token error [{token_id[:8]}]: {exc}")

                    self._cycle += 1
                    if self._cycle % _LOG_HEARTBEAT_CYCLES == 0:
                        log.info(
                            f"vpin_poller: heartbeat cycle={self._cycle} "
                            f"tracking {len(token_ids)} tokens"
                        )

                except Exception as exc:
                    log.exception(f"vpin_poller: top-level error (will retry): {exc}")

                await asyncio.sleep(VPIN_POLL_INTERVAL)
```

- [ ] **Step 5.4: Run tests**

```
pytest tests/maker/test_vpin_poller.py -v
```

Expected: all 8 tests PASS

- [ ] **Step 5.5: Run full suite**

```
pytest --tb=short -q
```

Expected: all pass

- [ ] **Step 5.6: Commit**

```bash
git add maker/vpin_poller.py tests/maker/test_vpin_poller.py
git commit -m "feat(vpin): add VPINPoller with size-weighted VPIN, EMA smoothing, and staleness guard"
```

---

## Task 6: Integrate `compute_regime_score()` into `QuoteEngine._reprice()`

**Files:**
- Modify: `maker/quote_engine.py`
- Modify: `tests/maker/test_quote_engine.py`

- [ ] **Step 6.1: Write failing behavioral tests**

Append to `tests/maker/test_quote_engine.py`:

```python
import asyncio
import pytest
from maker.regime import CATEGORY_SPREAD_MULTIPLIER, compute_regime_score
from market.state import ContractState, AppState
from maker.state import MakerState
from maker.markout_tracker import MarkoutTracker
from maker.quote_engine import QuoteEngine


def _make_cs_regime(token_id, bid=0.48, ask=0.52, category="crypto",
                    vpin=0.5, end_date_iso="2030-01-01T00:00:00Z") -> ContractState:
    cs = ContractState(
        yes_token_id=token_id, no_token_id="no-" + token_id,
        question="Will BTC be above $90k?", category=category,
        best_bid=bid, best_ask=ask, volume_usd=10_000.0,
        end_date_iso=end_date_iso,
    )
    cs.vpin = vpin
    return cs


@pytest.mark.asyncio
async def test_high_vpin_widens_spread_vs_neutral(tmp_path):
    """VPIN=0.9 should produce a wider effective spread than VPIN=0.5."""
    token_id = "tok-vpin"

    async def _get_spread(vpin_val):
        app = AppState()
        app.markets[token_id] = _make_cs_regime(token_id, vpin=vpin_val)
        ms = MakerState()
        ms.selected_token_ids = {token_id}
        intents_q: asyncio.Queue = asyncio.Queue()
        mt_q: asyncio.Queue = asyncio.Queue()
        mt = MarkoutTracker(app, ms, mt_q, log_path=str(tmp_path / f"m_{vpin_val}.jsonl"))
        qe = QuoteEngine(app, ms, asyncio.Queue(), intents_q, asyncio.Queue(),
                         markout_tracker=mt)
        qe._active_token_ids = {token_id}
        await qe._reprice({token_id}, force=True, new_ids={token_id})
        spreads = []
        while not intents_q.empty():
            ladder = intents_q.get_nowait()
            for lvl in ladder.levels:
                spreads.append(lvl.ask_price - lvl.bid_price)
        return sum(spreads) / len(spreads) if spreads else None

    spread_neutral = await _get_spread(0.5)
    spread_toxic   = await _get_spread(0.9)

    assert spread_neutral is not None
    assert spread_toxic is not None
    assert spread_toxic > spread_neutral


def test_long_inventory_produces_negative_skew():
    """Long inventory at 80% cap shades fair value downward."""
    d = compute_regime_score(vpin=0.5, markout_30s=0.0, inv_signed=0.8,
                             hours_to_resolution=24.0, category="crypto")
    assert d.skew_adjustment < 0.0
    assert abs(d.skew_adjustment) > 0.001


def test_weather_category_multiplier_is_2x_finance():
    """CATEGORY_SPREAD_MULTIPLIER['weather'] / ['finance'] == 2.0"""
    assert CATEGORY_SPREAD_MULTIPLIER["weather"] / CATEGORY_SPREAD_MULTIPLIER["finance"] == 2.0
```

- [ ] **Step 6.2: Run to confirm test discovery**

```
pytest tests/maker/test_quote_engine.py -k "vpin or skew or category_multiplier" -v
```

Expected: tests collected, `test_long_inventory` and `test_weather_category` pass immediately (pure function tests); `test_high_vpin_widens_spread` may fail if `markout_tracker` param not yet wired.

- [ ] **Step 6.3: Add `markout_tracker` parameter to `QuoteEngine.__init__`**

In `maker/quote_engine.py`, find `__init__` and add the parameter:

```python
    def __init__(
        self,
        app_state: AppState,
        maker_state: MakerState,
        active_markets_q: asyncio.Queue,
        quote_intents_q: asyncio.Queue,
        skew_updates_q: asyncio.Queue,
        price_update_q: asyncio.Queue | None = None,
        cancel_q: asyncio.Queue | None = None,
        markout_tracker=None,   # MarkoutTracker | None — avoids circular import
    ):
        # ... existing assignments unchanged ...
        self._markout_tracker = markout_tracker
```

Add imports at top of `maker/quote_engine.py`:

```python
from maker.regime import (
    compute_regime_score, CATEGORY_SPREAD_MULTIPLIER,
    MIN_SPREAD as REGIME_MIN_SPREAD, MAX_SPREAD as REGIME_MAX_SPREAD,
)
```

- [ ] **Step 6.4: Insert regime call into `_reprice()` in `maker/quote_engine.py`**

Inside `_reprice()`, find `inv = self._maker.get_inventory(token_id)` (around line 243) and insert the regime block immediately after `abs_inv = abs(inv)`:

```python
            # Regime score — single call, result reused for all four outputs
            inv_signed = self._maker.skew_factor(token_id, cs.category)
            markout_30s = (
                self._markout_tracker.get_markout_30s(token_id, cs.category)
                if self._markout_tracker is not None else 0.0
            )
            regime = compute_regime_score(
                vpin                = cs.vpin,
                markout_30s         = markout_30s,
                inv_signed          = inv_signed,
                hours_to_resolution = max(0.0, hours_to_expiry),
                category            = cs.category,
            )
            log.debug(
                f"regime[{token_id[:8]}] score={regime.score:.3f} "
                f"vpin={cs.vpin:.3f} markout={markout_30s:.4f} "
                f"inv={inv_signed:.2f} hours={hours_to_expiry:.1f} "
                f"spread_x={regime.spread_multiplier:.2f} size_x={regime.size_multiplier:.2f} "
                f"skew={regime.skew_adjustment:+.4f} "
                f"flags={','.join(sorted(regime.flags)) or 'none'}"
            )
            if "extreme" in regime.flags:
                log.warning(f"extreme regime [{token_id[:8]}] score={regime.score:.2f}")
            if "toxic_flow" in regime.flags:
                log.info(f"toxic_flow [{token_id[:8]}] vpin={cs.vpin:.2f}")
```

Note: `hours_to_expiry` is already computed above this block from `cs.end_date_iso`.

- [ ] **Step 6.5: Apply regime skew before model anchor, and regime multipliers to spread/size**

After `fv = compute_fair_value(...)` and inside the model anchor block, apply skew to `anchor_fv` just before the OBI step:

```python
            # Apply regime skew to anchor_fv BEFORE spread derivation
            anchor_fv = max(0.05, min(0.95, anchor_fv + regime.skew_adjustment))
```

Replace the line `spread = min(base_spread * spread_multiplier, MAX_SPREAD)` with:

```python
            # Category baseline x Falcon adverse-selection x regime dynamic multiplier
            cat_mult = CATEGORY_SPREAD_MULTIPLIER.get(cs.category, 1.5)
            spread = _clamp(
                base_spread * spread_multiplier * cat_mult * regime.spread_multiplier,
                REGIME_MIN_SPREAD,
                REGIME_MAX_SPREAD,
            )
```

In the `build_ladder` call, apply `size_multiplier`:

```python
            ladder = self.build_ladder(
                token_id=token_id,
                fair_value=eff_fv,
                spread=eff_spread,
                size=max(1.0, QUOTE_SIZE_USDC * regime.size_multiplier),
                reason="reprice",
            )
```

- [ ] **Step 6.6: Wire `markout_tracker` in `build_maker_actors` in `maker/runner.py`**

In `build_maker_actors`, build `markout_tracker_actor` before the `actors` dict and pass it to `QuoteEngine`:

```python
    from maker.markout_tracker import MarkoutTracker  # add at top of function or file

    markout_tracker_actor = MarkoutTracker(
        app_state, maker_state, markout_q,
        kill_switch_enabled=(not paper and not shadow),
    )

    actors = {
        "selector": MarketSelector(app_state, active_markets_q, maker_state=maker_state),
        "quote_engine": QuoteEngine(
            app_state, maker_state, active_markets_q, quote_intents_q, skew_updates_q,
            price_update_q=price_update_q,
            cancel_q=cancel_q,
            markout_tracker=markout_tracker_actor,
        ),
        # ... rest unchanged ...
        "markout_tracker": markout_tracker_actor,
    }
```

Remove the duplicate `MarkoutTracker(...)` construction that was previously inside the `actors` dict.

- [ ] **Step 6.7: Run all maker tests**

```
pytest tests/maker/ -v --tb=short
```

Expected: all pass

- [ ] **Step 6.8: Commit**

```bash
git add maker/quote_engine.py maker/runner.py tests/maker/test_quote_engine.py
git commit -m "feat(quote_engine): integrate RegimeDecision — single compute_regime_score() call per market"
```

---

## Task 7: Start `VPINPoller` in `maker/runner.py`

**Files:**
- Modify: `maker/runner.py`

- [ ] **Step 7.1: Add VPINPoller wrapper and import**

In `maker/runner.py`, add import at the top:

```python
from maker.vpin_poller import VPINPoller
```

Add the isolated wrapper coroutine at module level (after imports, before `build_maker_actors`):

```python
async def _run_vpin_poller(poller: VPINPoller) -> None:
    """Isolated wrapper — VPINPoller crash does not propagate to asyncio.gather."""
    while True:
        try:
            await poller.run()
        except Exception as exc:
            log.exception(f"VPINPoller crashed (restarting in 30s): {exc}")
            await asyncio.sleep(30.0)
```

In `run_maker()`, after the `coros` list is populated and before `asyncio.gather(*coros)`:

```python
    vpin_poller = VPINPoller(app_state)
    coros.append(_run_vpin_poller(vpin_poller))
```

- [ ] **Step 7.2: Run integration tests**

```
pytest tests/maker/test_runner.py tests/maker/test_integration.py -v
```

Expected: all pass

- [ ] **Step 7.3: Run full suite**

```
pytest --tb=short -q
```

Expected: all tests pass

- [ ] **Step 7.4: Commit**

```bash
git add maker/runner.py
git commit -m "feat(runner): start VPINPoller as isolated asyncio task alongside maker actors"
```

---

## Task 8: Final verification

- [ ] **Step 8.1: Run full test suite**

```
pytest -v --tb=short 2>&1 | tail -40
```

Expected: no failures

- [ ] **Step 8.2: Verify neutral regime is identity (no regression)**

```bash
python -c "
from maker.regime import compute_regime_score
d = compute_regime_score(0.5, 0.0, 0.0, 48.0, 'crypto')
assert abs(d.spread_multiplier - 1.0) < 0.05, d.spread_multiplier
assert abs(d.size_multiplier - 1.0) < 0.05, d.size_multiplier
assert d.skew_adjustment == 0.0, d.skew_adjustment
assert d.flags == frozenset(), d.flags
print('OK neutral regime is identity')
"
```

- [ ] **Step 8.3: Push branch**

```bash
git push origin feat/v3-multi-asset-expansion
```

---

## Tuning Reference (post-deploy, not implementation tasks)

| Parameter | File | Default | Adjustment trigger |
|---|---|---|---|
| VPIN denominator | `maker/regime.py` | 0.3 | Regime feels twitchy → increase to 0.4 |
| Markout divisor | `maker/regime.py` | 0.05 | Overreacts to noise → increase to 0.08 |
| Time exponent | `maker/regime.py` | 1.5 | Near-resolution losses persist → increase to 2.0 |
| MAX_SPREAD | `maker/regime.py` | 0.15 | Weather/entertainment still leaking → increase to 0.20 |
| Skew cap | `maker/regime.py` | 0.02 | Inventory unwinds too slowly → increase to 0.03 |
| VPIN_STALE_SECONDS | `maker/vpin_poller.py` | 180 | Tune with observed poll latency |
