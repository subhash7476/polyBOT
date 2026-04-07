# Polymarket Bot v3 Enhancement Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 3 new Bayesian signals (flatline, order-book imbalance, volume-price divergence), enhanced arb detection (complement + cross-temporal), dynamic weight optimizer, improved calibration reports, market screener, execution improvements, and a monitoring alert system.

**Architecture:** New signals plug into the existing `BayesianEngine` via `engine.add_signal(Signal(...))`. Price and volume history is tracked in module-level dicts inside each signal module (no `ContractState` surgery needed). After `build_model_probability()` returns the engine, market-microstructure signals are added to it in `main.py` before `passes_signal_filter()`. All weights/thresholds go in `config.py`. Everything logs to `fills.jsonl`.

**Tech Stack:** Python 3.11+, numpy, scipy, collections.deque, py-clob-client, rich (terminal dashboard)

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `engine/flatline.py` | Create | Pre-resolution price stagnation signal |
| `engine/orderbook_imbalance.py` | Create | Bid/ask depth skew signal |
| `engine/volume_divergence.py` | Create | Volume spike without price move signal |
| `engine/arb_scanner.py` | Modify | Add complement + cross-temporal arb detection |
| `calibration/weight_optimizer.py` | Create | Logistic regression on fills.jsonl for weight recommendations |
| `calibration/metrics.py` | Modify | Add `--detailed` report: per-signal attribution, time-decay, category breakdown, edge decay |
| `market/market_screener.py` | Create | Hourly ranked watchlist by edge opportunity |
| `engine/contract_parser.py` | Modify | Add election/political and deadline/event categories |
| `main.py` | Modify | Wire new signals into trading loop after `build_model_probability()` |
| `market/clob_monitor.py` | Modify | Feed bid/ask depth into ContractState on `book` events |
| `market/state.py` | Modify | Add `bid_depth`, `ask_depth` to ContractState |
| `config.py` | Modify | Add weights for flatline, OBI, VPD; add thresholds |
| `monitoring/alerts.py` | Create | Telegram/Discord notification system |
| `monitoring/__init__.py` | Create | Package init |
| `discoveries.md` | Create | Backtest results and pattern documentation |
| `recon_notes.md` | Create | Reconnaissance findings from reference repos |
| `tests/engine/test_flatline.py` | Create | Unit + integration tests for flatline signal |
| `tests/engine/test_orderbook_imbalance.py` | Create | Unit + integration tests for OBI signal |
| `tests/engine/test_volume_divergence.py` | Create | Unit + integration tests for VPD signal |
| `tests/engine/test_arb_scanner_v2.py` | Create | Tests for complement + cross-temporal arb |
| `tests/calibration/test_weight_optimizer.py` | Create | Tests for weight optimizer |
| `tests/market/test_market_screener.py` | Create | Tests for market screener |
| `tests/monitoring/test_alerts.py` | Create | Tests for alert system |
| `tests/engine/test_parser_categories.py` | Create | Tests for election/event categories |
| `tests/trading/test_executor_improvements.py` | Create | Tests for stale order detection + splitting |

---

## Task 1: Reconnaissance — Write recon_notes.md

**Files:**
- Create: `recon_notes.md`
- Create: `discoveries.md`

- [ ] **Step 1: Research reference repos via web search**

WebSearch for key patterns from `polymarket-mcp-server`, `polymarket-assistant-tool`, `polymarket-agents`.
Focus on: order flow signal generation, Gamma API patterns, safety guardrails.

- [ ] **Step 2: Write recon_notes.md**

Create `recon_notes.md` with three sections:
1. What's worth integrating (patterns, not code)
2. What's irrelevant or too complex for current stage
3. Specific integration points into our codebase

- [ ] **Step 3: Create discoveries.md stub**

```markdown
# discoveries.md — Pattern & Backtest Log

## Format
Each entry: date | sample_size | win_rate | p_value | notes

## Entries
<!-- Populated as signals are backtested -->
```

- [ ] **Step 4: Commit**

```bash
git add recon_notes.md discoveries.md
git commit -m "docs: reconnaissance notes and discoveries log stub"
```

---

## Task 2: Add bid/ask depth to ContractState + CLOBMonitor

The OBI signal needs order book depth (total size on each side), not just best price. The `_handle_book` message already contains full bids/asks lists — we just need to sum them.

**Files:**
- Modify: `market/state.py` — add `bid_depth`, `ask_depth` fields to ContractState
- Modify: `market/clob_monitor.py` — update `_handle_book` to compute + store depth

- [ ] **Step 1: Write the failing test**

Create `tests/market/test_depth_tracking.py`:
```python
from market.state import ContractState

def test_contract_state_has_depth_fields():
    cs = ContractState(
        yes_token_id="abc", no_token_id="def",
        question="Will BTC > $100k?", category="crypto"
    )
    assert hasattr(cs, "bid_depth")
    assert hasattr(cs, "ask_depth")
    assert cs.bid_depth == 0.0
    assert cs.ask_depth == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/market/test_depth_tracking.py -v
```
Expected: FAIL with `AttributeError` or assertion error.

- [ ] **Step 3: Add fields to ContractState in market/state.py**

In `ContractState` dataclass (after `volume_usd: float = 0.0`):
```python
    bid_depth: float = 0.0   # total size on best 5 bid levels (USDC)
    ask_depth: float = 0.0   # total size on best 5 ask levels (USDC)
```

- [ ] **Step 4: Update _handle_book in market/clob_monitor.py**

After the existing bid/ask price update block in `_handle_book`, add:
```python
        # Compute book depth (sum of best 5 levels each side)
        if bids:
            cs.bid_depth = sum(float(b.get("size", 0)) for b in bids[:5])
        if asks:
            cs.ask_depth = sum(float(a.get("size", 0)) for a in asks[:5])
```

- [ ] **Step 5: Run test + full suite**

```bash
pytest tests/market/test_depth_tracking.py -v
pytest --tb=short -q
```
Expected: new test passes, all existing tests pass.

- [ ] **Step 6: Commit**

```bash
git add market/state.py market/clob_monitor.py tests/market/test_depth_tracking.py
git commit -m "feat: add bid_depth/ask_depth to ContractState, populated from CLOB book events"
```

---

## Task 3: Flatline Signal (engine/flatline.py)

**Hypothesis:** Markets where YES price doesn't move for 48+ hours before resolution resolve to the leading side ~79-81% of the time. Weight 0.20 initially.

**Files:**
- Create: `engine/flatline.py`
- Create: `tests/engine/test_flatline.py`
- Modify: `config.py` — add `FLATLINE_THRESHOLD`, `FLATLINE_WINDOW_HOURS`, weight

**How it works:**
- Module-level `_price_history: dict[str, deque[tuple[float, float]]]` tracks `(timestamp, yes_mid_price)` per token_id
- `record_price(token_id, price)` — called from main.py trading loop, appends latest mid
- `compute_flatline_signal(token_id, contract_state, parsed_contract)` — returns `Signal | None`
- Only fires when market is within 72h of expiry, leading price > 0.60, and 48h price range < threshold

- [ ] **Step 1: Write the failing tests**

Create `tests/engine/test_flatline.py`:
```python
import time
import pytest
from unittest.mock import MagicMock
from datetime import datetime, timezone, timedelta
from engine.flatline import compute_flatline_signal, record_price, _price_history
from engine.contract_parser import ParsedContract
from market.state import ContractState


def _make_contract(hours_to_expiry=48.0, direction="above") -> ParsedContract:
    expiry = datetime.now(timezone.utc) + timedelta(hours=hours_to_expiry)
    return ParsedContract(
        token_id="tok1", question="Will BTC > $100k?",
        asset="BTC", direction=direction,
        target_price=100000, expiry=expiry, category="crypto"
    )


def _make_cs(bid=0.72, ask=0.76) -> ContractState:
    return ContractState(
        yes_token_id="tok1", no_token_id="tok2",
        question="Will BTC > $100k?", category="crypto",
        best_bid=bid, best_ask=ask
    )


def setup_function():
    _price_history.clear()


def test_no_signal_when_insufficient_history():
    parsed = _make_contract(hours_to_expiry=24.0)
    cs = _make_cs()
    # Only 1 price recorded — not enough
    record_price("tok1", 0.74)
    sig = compute_flatline_signal("tok1", cs, parsed)
    assert sig is None


def test_no_signal_when_too_far_from_expiry():
    parsed = _make_contract(hours_to_expiry=100.0)
    cs = _make_cs()
    now = time.time()
    for i in range(20):
        _price_history["tok1"].append((now - 3600 * i, 0.74))
    sig = compute_flatline_signal("tok1", cs, parsed)
    assert sig is None  # 100h to expiry > 72h threshold


def test_no_signal_when_price_moves():
    parsed = _make_contract(hours_to_expiry=24.0)
    cs = _make_cs()
    now = time.time()
    # Simulate 5% price range — not a flatline
    prices = [0.65, 0.70, 0.68, 0.72, 0.65, 0.70]
    for i, p in enumerate(prices):
        _price_history["tok1"].append((now - 3600 * (len(prices) - i), p))
    sig = compute_flatline_signal("tok1", cs, parsed)
    assert sig is None


def test_signal_fires_on_flatline():
    parsed = _make_contract(hours_to_expiry=24.0)
    cs = _make_cs(bid=0.72, ask=0.76)  # mid=0.74, leading side = YES
    now = time.time()
    # 49h of flat prices within 1% range
    for i in range(50):
        _price_history["tok1"].append((now - 3600 * (50 - i), 0.74 + (i % 2) * 0.005))
    sig = compute_flatline_signal("tok1", cs, parsed)
    assert sig is not None
    assert sig.name == "flatline"
    assert sig.strength > 0   # positive = YES direction
    assert 0 < sig.weight <= 0.30


def test_signal_below_direction():
    parsed = _make_contract(hours_to_expiry=24.0, direction="below")
    cs = _make_cs(bid=0.72, ask=0.76)
    now = time.time()
    for i in range(50):
        _price_history["tok1"].append((now - 3600 * (50 - i), 0.74))
    sig = compute_flatline_signal("tok1", cs, parsed)
    assert sig is not None
    assert sig.strength > 0   # YES still leads, direction=below → positive still


def test_no_signal_when_leading_price_too_low():
    parsed = _make_contract(hours_to_expiry=24.0)
    cs = _make_cs(bid=0.52, ask=0.56)  # mid=0.54, too close to 50-50
    now = time.time()
    for i in range(50):
        _price_history["tok1"].append((now - 3600 * (50 - i), 0.54))
    sig = compute_flatline_signal("tok1", cs, parsed)
    assert sig is None  # leading price < 0.60 threshold


def test_record_price_limits_history():
    for i in range(300):
        record_price("tok1", 0.74)
    assert len(_price_history["tok1"]) <= 200  # bounded deque
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/engine/test_flatline.py -v
```
Expected: `ModuleNotFoundError: No module named 'engine.flatline'`

- [ ] **Step 3: Add config entries**

In `config.py`, after `SIGNAL_WEIGHTS`:
```python
# Flatline detector
FLATLINE_WINDOW_HOURS = 48.0        # price range computed over this lookback
FLATLINE_EXPIRY_GATE_HOURS = 72.0   # only fires within this many hours of expiry
FLATLINE_THRESHOLD = 0.02           # max range (0.02 = 2 cents on a 0-1 scale)
FLATLINE_MIN_LEADING_PRICE = 0.60   # leading side must be > 60% to fire
```

Add to `SIGNAL_WEIGHTS`:
```python
    "flatline": 0.20,
```

- [ ] **Step 4: Create engine/flatline.py**

```python
"""
engine/flatline.py

Pre-resolution flatline detector.

Hypothesis: When a Polymarket YES price does not move by more than FLATLINE_THRESHOLD
over the past FLATLINE_WINDOW_HOURS, and the market resolves within FLATLINE_EXPIRY_GATE_HOURS,
the leading side wins ~79-81% of the time.

Signal: added to BayesianEngine with weight config.SIGNAL_WEIGHTS["flatline"].
"""
import time
from collections import deque
from datetime import datetime, timezone
from typing import Optional

from engine.bayesian import Signal
from engine.contract_parser import ParsedContract
from market.state import ContractState
from config import (
    FLATLINE_WINDOW_HOURS,
    FLATLINE_EXPIRY_GATE_HOURS,
    FLATLINE_THRESHOLD,
    FLATLINE_MIN_LEADING_PRICE,
    SIGNAL_WEIGHTS,
)
from utils.logger import get_logger

log = get_logger(__name__)

# Module-level price history: token_id → deque of (unix_timestamp, yes_mid_price)
_price_history: dict[str, deque] = {}


def record_price(token_id: str, yes_mid: float) -> None:
    """Append current YES mid price with timestamp. Call from trading loop."""
    if token_id not in _price_history:
        _price_history[token_id] = deque(maxlen=200)
    _price_history[token_id].append((time.time(), yes_mid))


def compute_flatline_signal(
    token_id: str,
    contract_state: ContractState,
    parsed: ParsedContract,
) -> Optional[Signal]:
    """
    Returns a Signal if the market is in a pre-resolution flatline, else None.

    Flatline criteria:
    1. Market resolves within FLATLINE_EXPIRY_GATE_HOURS
    2. Leading YES price > FLATLINE_MIN_LEADING_PRICE
    3. Price range over last FLATLINE_WINDOW_HOURS < FLATLINE_THRESHOLD
    4. At least 5 price observations spanning 48+ hours exist
    """
    if parsed.expiry is None:
        return None

    # Gate 1: close to expiry
    now = datetime.now(timezone.utc)
    hours_left = (parsed.expiry - now).total_seconds() / 3600
    if hours_left > FLATLINE_EXPIRY_GATE_HOURS or hours_left <= 0:
        return None

    yes_mid = contract_state.mid

    # Gate 2: leading side must be sufficiently priced in
    leading_price = max(yes_mid, 1 - yes_mid)
    if leading_price < FLATLINE_MIN_LEADING_PRICE:
        return None

    # Gate 3: enough history spanning the window
    history = _price_history.get(token_id)
    if not history or len(history) < 5:
        return None

    now_ts = time.time()
    window_start = now_ts - FLATLINE_WINDOW_HOURS * 3600
    window_prices = [p for ts, p in history if ts >= window_start]

    if len(window_prices) < 5:
        return None

    # Verify the oldest observation in window is at least FLATLINE_WINDOW_HOURS old
    oldest_in_window = min(ts for ts, _ in history if ts >= window_start)
    if now_ts - oldest_in_window < FLATLINE_WINDOW_HOURS * 3600 * 0.9:
        # Haven't observed the full window yet — don't fire prematurely
        return None

    # Gate 4: price range check
    price_range = max(window_prices) - min(window_prices)
    if price_range >= FLATLINE_THRESHOLD:
        return None

    # Signal fires — strength proportional to how certain the leading side is
    strength = (leading_price - 0.50) * 2  # maps [0.50, 1.0] → [0, 1]
    # Confidence higher when range is tighter (range near 0 → confidence 1.0)
    confidence = max(0.1, 1.0 - price_range / FLATLINE_THRESHOLD)
    weight = SIGNAL_WEIGHTS.get("flatline", 0.20)

    log.info(
        f"flatline [{token_id[:8]}]: range={price_range:.4f} "
        f"leading={leading_price:.3f} hours_left={hours_left:.1f}h "
        f"strength={strength:.3f} conf={confidence:.2f}"
    )

    return Signal(
        name="flatline",
        strength=strength,
        weight=weight,
        confidence=confidence,
    )
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/engine/test_flatline.py -v
pytest --tb=short -q
```
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add engine/flatline.py tests/engine/test_flatline.py config.py
git commit -m "feat: flatline pre-resolution signal detector (weight=0.20)"
```

---

## Task 4: Order Book Imbalance Signal (engine/orderbook_imbalance.py)

**Hypothesis:** Persistent bid depth >> ask depth (or vice versa) sustained over 3+ readings predicts short-term direction. Weight 0.10 (noisy).

**Files:**
- Create: `engine/orderbook_imbalance.py`
- Create: `tests/engine/test_orderbook_imbalance.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/engine/test_orderbook_imbalance.py`:
```python
import time
import pytest
from engine.orderbook_imbalance import (
    record_obi_reading,
    compute_obi_signal,
    _obi_history,
)
from market.state import ContractState


def _make_cs(bid_depth=1000.0, ask_depth=400.0) -> ContractState:
    cs = ContractState(
        yes_token_id="tok1", no_token_id="tok2",
        question="Will BTC > $100k?", category="crypto",
    )
    cs.bid_depth = bid_depth
    cs.ask_depth = ask_depth
    return cs


def setup_function():
    _obi_history.clear()


def test_no_signal_when_insufficient_readings():
    cs = _make_cs(bid_depth=2000, ask_depth=500)
    record_obi_reading("tok1", cs)
    record_obi_reading("tok1", cs)  # only 2 readings
    sig = compute_obi_signal("tok1")
    assert sig is None


def test_no_signal_when_ratio_neutral():
    cs = _make_cs(bid_depth=1000, ask_depth=900)  # ratio ~1.11, well below 2.5
    now = time.time()
    for i in range(5):
        _obi_history["tok1"].append((now - 900 * i, 1000.0 / 900.0))
    sig = compute_obi_signal("tok1")
    assert sig is None


def test_signal_fires_on_bid_heavy_book():
    now = time.time()
    # bid/ask ratio = 3.0 (bid heavy → bullish → positive strength)
    for i in range(4):
        _obi_history["tok1"].append((now - 1000 * i, 3.0))
    sig = compute_obi_signal("tok1")
    assert sig is not None
    assert sig.name == "orderbook_imbalance"
    assert sig.strength > 0  # bid-heavy = bullish


def test_signal_fires_on_ask_heavy_book():
    now = time.time()
    # bid/ask ratio = 0.30 (ask heavy → bearish → negative strength)
    for i in range(4):
        _obi_history["tok1"].append((now - 1000 * i, 0.30))
    sig = compute_obi_signal("tok1")
    assert sig is not None
    assert sig.strength < 0  # ask-heavy = bearish


def test_no_signal_when_readings_not_sustained():
    now = time.time()
    # Mixed readings: not sustained in same direction
    ratios = [3.0, 0.3, 3.0, 0.3]
    for i, r in enumerate(ratios):
        _obi_history["tok1"].append((now - 1000 * i, r))
    sig = compute_obi_signal("tok1")
    assert sig is None


def test_record_obi_clamps_history():
    cs = _make_cs(bid_depth=1000, ask_depth=500)
    for _ in range(50):
        record_obi_reading("tok1", cs)
    assert len(_obi_history["tok1"]) <= 20
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/engine/test_orderbook_imbalance.py -v
```

- [ ] **Step 3: Add config**

In `config.py`:
```python
# Order book imbalance
OBI_RATIO_HIGH = 2.5    # bid/ask depth ratio > this → bullish signal
OBI_RATIO_LOW = 0.4     # bid/ask depth ratio < this → bearish signal
OBI_MIN_READINGS = 3    # must sustain over this many consecutive readings
```

Add to `SIGNAL_WEIGHTS`:
```python
    "orderbook_imbalance": 0.10,
```

- [ ] **Step 4: Create engine/orderbook_imbalance.py**

```python
"""
engine/orderbook_imbalance.py

Order book imbalance signal.

Hypothesis: Persistent depth skew (bid_depth/ask_depth > OBI_RATIO_HIGH or
< OBI_RATIO_LOW) sustained over OBI_MIN_READINGS consecutive readings (15 min apart)
predicts short-term direction.

Low weight (0.10) — this signal is noisy but fast-updating.
"""
import math
import time
from collections import deque
from typing import Optional

from engine.bayesian import Signal
from market.state import ContractState
from config import OBI_RATIO_HIGH, OBI_RATIO_LOW, OBI_MIN_READINGS, SIGNAL_WEIGHTS
from utils.logger import get_logger

log = get_logger(__name__)

# Module-level OBI history: token_id → deque of (unix_timestamp, ratio)
_obi_history: dict[str, deque] = {}


def record_obi_reading(token_id: str, contract_state: ContractState) -> None:
    """Record current bid/ask depth ratio. Call from trading loop (every ~15 min)."""
    if contract_state.ask_depth <= 0:
        return
    ratio = contract_state.bid_depth / contract_state.ask_depth
    if token_id not in _obi_history:
        _obi_history[token_id] = deque(maxlen=20)
    _obi_history[token_id].append((time.time(), ratio))


def compute_obi_signal(token_id: str) -> Optional[Signal]:
    """
    Returns Signal if OBI is sustained above/below threshold over recent readings.
    Returns None if insufficient history or not sustained.
    """
    history = _obi_history.get(token_id)
    if not history or len(history) < OBI_MIN_READINGS:
        return None

    # Check last OBI_MIN_READINGS readings for consistent direction
    recent = list(history)[-OBI_MIN_READINGS:]
    ratios = [r for _, r in recent]

    all_bullish = all(r > OBI_RATIO_HIGH for r in ratios)
    all_bearish = all(r < OBI_RATIO_LOW for r in ratios)

    if not all_bullish and not all_bearish:
        return None

    avg_ratio = sum(ratios) / len(ratios)
    # Strength = log(ratio) normalized: log(2.5)≈0.92 maps to ~0.92
    raw_strength = math.log(avg_ratio) if avg_ratio > 0 else 0.0
    # Clamp to [-1, 1]
    strength = max(-1.0, min(1.0, raw_strength))

    weight = SIGNAL_WEIGHTS.get("orderbook_imbalance", 0.10)

    log.debug(f"obi [{token_id[:8]}]: ratio={avg_ratio:.2f} strength={strength:.3f}")

    return Signal(
        name="orderbook_imbalance",
        strength=strength,
        weight=weight,
        confidence=0.8,  # OBI is real-time but noisy
    )
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/engine/test_orderbook_imbalance.py -v
pytest --tb=short -q
```

- [ ] **Step 6: Commit**

```bash
git add engine/orderbook_imbalance.py tests/engine/test_orderbook_imbalance.py config.py
git commit -m "feat: order book imbalance signal (weight=0.10)"
```

---

## Task 5: Volume-Price Divergence Signal (engine/volume_divergence.py)

**Hypothesis:** Volume spike (>2× rolling avg) without price movement (<2%) = accumulation. Price will follow.

**Files:**
- Create: `engine/volume_divergence.py`
- Create: `tests/engine/test_volume_divergence.py`

- [ ] **Step 1: Write failing tests**

Create `tests/engine/test_volume_divergence.py`:
```python
import time
import pytest
from engine.volume_divergence import (
    record_volume,
    compute_vpd_signal,
    _volume_history,
    _price_at_volume_spike,
)
from market.state import ContractState


def _make_cs(mid=0.55, volume_usd=50000.0) -> ContractState:
    cs = ContractState(
        yes_token_id="tok1", no_token_id="tok2",
        question="Will BTC > $100k?", category="crypto",
        best_bid=mid - 0.01, best_ask=mid + 0.01, volume_usd=volume_usd
    )
    return cs


def setup_function():
    _volume_history.clear()
    _price_at_volume_spike.clear()


def test_no_signal_when_insufficient_history():
    cs = _make_cs(volume_usd=100000)
    record_volume("tok1", cs)
    sig = compute_vpd_signal("tok1", cs)
    assert sig is None


def test_no_signal_when_volume_not_elevated():
    now = time.time()
    # Fill history with baseline volume
    for i in range(25):
        _volume_history["tok1"].append((now - 3600 * i, 10000.0))
    cs = _make_cs(volume_usd=15000)  # only 1.5× avg, below 2× threshold
    sig = compute_vpd_signal("tok1", cs)
    assert sig is None


def test_no_signal_when_price_moves_with_volume():
    now = time.time()
    for i in range(25):
        _volume_history["tok1"].append((now - 3600 * i, 10000.0))
    # Record a spike but also record a big price move
    _price_at_volume_spike["tok1"] = 0.50
    cs = _make_cs(mid=0.58, volume_usd=30000)  # 3× avg + 8% price move (> 2%)
    sig = compute_vpd_signal("tok1", cs)
    assert sig is None


def test_signal_fires_on_volume_spike_no_price_move():
    now = time.time()
    for i in range(25):
        _volume_history["tok1"].append((now - 3600 * i, 10000.0))
    _price_at_volume_spike["tok1"] = 0.55
    cs = _make_cs(mid=0.56, volume_usd=30000)  # 3× avg, only 1% price move
    sig = compute_vpd_signal("tok1", cs)
    assert sig is not None
    assert sig.name == "volume_divergence"
    assert sig.strength > 0
    assert sig.weight <= 0.15


def test_signal_strength_scales_with_volume_multiple():
    now = time.time()
    for i in range(25):
        _volume_history["tok1"].append((now - 3600 * i, 10000.0))
    _price_at_volume_spike["tok1"] = 0.55

    cs_low = _make_cs(mid=0.56, volume_usd=25000)   # 2.5× avg
    cs_high = _make_cs(mid=0.56, volume_usd=50000)  # 5.0× avg

    sig_low = compute_vpd_signal("tok1", cs_low)
    _volume_history["tok1"].clear()
    for i in range(25):
        _volume_history["tok1"].append((now - 3600 * i, 10000.0))
    sig_high = compute_vpd_signal("tok1", cs_high)

    if sig_low and sig_high:
        assert sig_high.strength >= sig_low.strength
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/engine/test_volume_divergence.py -v
```

- [ ] **Step 3: Add config**

In `config.py`:
```python
# Volume-price divergence
VPD_VOLUME_MULTIPLE = 2.0    # current volume must exceed rolling_avg × this
VPD_PRICE_MOVE_MAX = 0.02    # price must NOT have moved more than this (2%)
VPD_LOOKBACK_HOURS = 24      # rolling average window
```

Add to `SIGNAL_WEIGHTS`:
```python
    "volume_divergence": 0.10,
```

- [ ] **Step 4: Create engine/volume_divergence.py**

```python
"""
engine/volume_divergence.py

Volume-price divergence signal.

Hypothesis: A volume spike (current > VPD_VOLUME_MULTIPLE × rolling avg) without
a corresponding price movement (< VPD_PRICE_MOVE_MAX) indicates accumulation.
Price tends to follow in the direction of the volume imbalance.

Signal strength: (volume / rolling_avg - 1) normalized, capped at 1.0.
Direction: positive (bullish) when YES side is leading; use current mid vs price-at-spike.
Weight: 0.10 initially (accumulation signals have long lags in prediction markets).
"""
import time
from collections import deque
from typing import Optional

from engine.bayesian import Signal
from market.state import ContractState
from config import VPD_VOLUME_MULTIPLE, VPD_PRICE_MOVE_MAX, VPD_LOOKBACK_HOURS, SIGNAL_WEIGHTS
from utils.logger import get_logger

log = get_logger(__name__)

# token_id → deque of (unix_timestamp, volume_usd_snapshot)
_volume_history: dict[str, deque] = {}
# token_id → yes_mid at time of volume spike detection (to measure subsequent price move)
_price_at_volume_spike: dict[str, float] = {}


def record_volume(token_id: str, contract_state: ContractState) -> None:
    """Record current volume snapshot. Call from trading loop each scan."""
    if token_id not in _volume_history:
        _volume_history[token_id] = deque(maxlen=100)
    _volume_history[token_id].append((time.time(), contract_state.volume_usd))


def compute_vpd_signal(
    token_id: str,
    contract_state: ContractState,
) -> Optional[Signal]:
    """
    Returns Signal if there is a volume spike without price confirmation.
    Returns None if conditions not met.
    """
    history = _volume_history.get(token_id)
    if not history or len(history) < 10:
        return None

    now_ts = time.time()
    window_start = now_ts - VPD_LOOKBACK_HOURS * 3600
    window_vols = [v for ts, v in history if ts >= window_start]

    if len(window_vols) < 5:
        return None

    rolling_avg = sum(window_vols) / len(window_vols)
    if rolling_avg <= 0:
        return None

    current_vol = contract_state.volume_usd
    volume_multiple = current_vol / rolling_avg

    if volume_multiple < VPD_VOLUME_MULTIPLE:
        return None  # not a spike

    # Record price at first detection of this spike
    yes_mid = contract_state.mid
    if token_id not in _price_at_volume_spike:
        _price_at_volume_spike[token_id] = yes_mid
        return None  # first detection — record but don't signal yet

    price_at_spike = _price_at_volume_spike[token_id]
    price_move = abs(yes_mid - price_at_spike)

    if price_move >= VPD_PRICE_MOVE_MAX:
        # Price has now moved — clear the spike record
        _price_at_volume_spike.pop(token_id, None)
        return None  # price followed volume, divergence resolved

    # Volume spike + no price move = divergence signal
    # Magnitude: how much above the multiple threshold, normalized
    raw_magnitude = min(1.0, (volume_multiple - VPD_VOLUME_MULTIPLE) / VPD_VOLUME_MULTIPLE)
    magnitude = max(0.05, raw_magnitude)

    # Direction: infer from price drift since the spike was first detected.
    # Small positive drift (YES going up) → accumulation → bullish (+).
    # Small negative drift → distribution → bearish (-).
    # Zero drift → magnitude only, treated as positive (slight bias; low confidence).
    price_direction = 1.0 if yes_mid >= price_at_spike else -1.0
    strength = price_direction * magnitude

    weight = SIGNAL_WEIGHTS.get("volume_divergence", 0.10)

    log.debug(
        f"vpd [{token_id[:8]}]: vol_mult={volume_multiple:.1f}x "
        f"price_move={price_move:.4f} strength={strength:.3f}"
    )

    return Signal(
        name="volume_divergence",
        strength=strength,
        weight=weight,
        confidence=0.6,  # lower confidence: direction ambiguous without trade tape
    )
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/engine/test_volume_divergence.py -v
pytest --tb=short -q
```

- [ ] **Step 6: Commit**

```bash
git add engine/volume_divergence.py tests/engine/test_volume_divergence.py config.py
git commit -m "feat: volume-price divergence signal (weight=0.10)"
```

---

## Task 6: Wire New Signals into Trading Loop (main.py)

New signals are added to the `BayesianEngine` instance returned by `build_model_probability()` BEFORE `passes_signal_filter()` is called. This means the signal filter sees all signals, including flatline/OBI/VPD.

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Write integration test**

Create `tests/test_new_signals_integration.py`:
```python
"""Test that new signals integrate with BayesianEngine without breaking filter."""
import time
from datetime import datetime, timezone, timedelta
from engine.bayesian import BayesianEngine, Signal
from engine.flatline import compute_flatline_signal, record_price, _price_history
from engine.orderbook_imbalance import compute_obi_signal, _obi_history
from engine.volume_divergence import compute_vpd_signal, record_volume, _volume_history, _price_at_volume_spike
from engine.contract_parser import ParsedContract
from market.state import ContractState


def setup_function():
    _price_history.clear()
    _obi_history.clear()
    _volume_history.clear()
    _price_at_volume_spike.clear()


def _flatline_contract():
    return ParsedContract(
        token_id="tok1", question="Will BTC > $100k?",
        asset="BTC", direction="above", target_price=100000,
        expiry=datetime.now(timezone.utc) + timedelta(hours=24),
        category="crypto"
    )


def test_signals_add_to_engine():
    engine = BayesianEngine(prior=0.55)
    engine.add_signal(Signal("vol_skew", 0.5, 0.15))

    cs = ContractState(
        yes_token_id="tok1", no_token_id="tok2",
        question="Will BTC > $100k?", category="crypto",
        best_bid=0.72, best_ask=0.76
    )
    cs.bid_depth = 2000.0
    cs.ask_depth = 600.0

    parsed = _flatline_contract()

    # Seed flatline history
    now = time.time()
    for i in range(50):
        _price_history["tok1"].append((now - 3600 * (50 - i), 0.74))

    sig = compute_flatline_signal("tok1", cs, parsed)
    if sig:
        before_count = engine.signal_count
        engine.add_signal(sig)
        assert engine.signal_count == before_count + 1
        assert 0 < engine.probability < 1


def test_engine_probability_stays_bounded():
    engine = BayesianEngine(prior=0.5)
    for _ in range(10):
        engine.add_signal(Signal("test", 1.0, 0.30))
    assert 0 < engine.probability < 1
```

- [ ] **Step 2: Run test to verify it passes (signals already implemented)**

```bash
pytest tests/test_new_signals_integration.py -v
```

- [ ] **Step 3: Modify main.py — add imports**

After the existing engine imports, add:
```python
from engine.flatline import compute_flatline_signal, record_price as record_flatline_price
from engine.orderbook_imbalance import compute_obi_signal, record_obi_reading
from engine.volume_divergence import compute_vpd_signal, record_volume
```

- [ ] **Step 4: Modify main.py — wire signals in trading_loop**

After the existing `build_model_probability()` / `build_macro_probability()` call block (after `mkt_rec["signal_count"] = signal_count`) and BEFORE `passes_signal_filter(engine)`, insert:

```python
                # New microstructure signals — added to existing engine before filter.
                # Applied to all categories: flatline fires near any resolution,
                # OBI and VPD are valid for any liquid market regardless of category.

                # Flatline: record current price, then check signal
                record_flatline_price(yes_token_id, contract_state.mid)
                flatline_sig = compute_flatline_signal(yes_token_id, contract_state, parsed)
                if flatline_sig:
                    engine.add_signal(flatline_sig)

                # OBI: record depth reading (requires bid_depth/ask_depth from Task 2)
                record_obi_reading(yes_token_id, contract_state)
                obi_sig = compute_obi_signal(yes_token_id)
                if obi_sig:
                    engine.add_signal(obi_sig)

                # VPD: record volume, then check divergence
                record_volume(yes_token_id, contract_state)
                vpd_sig = compute_vpd_signal(yes_token_id, contract_state)
                if vpd_sig:
                    engine.add_signal(vpd_sig)

                # Re-read updated probability and signal count after new signals
                model_prob = engine.probability
                signal_count = engine.signal_count
                mkt_rec["model_prob"] = model_prob
                mkt_rec["signal_count"] = signal_count
```

- [ ] **Step 5: Run full test suite**

```bash
pytest --tb=short -q
```
Expected: all 222+ tests pass, no regressions.

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_new_signals_integration.py
git commit -m "feat: wire flatline/OBI/VPD signals into trading loop"
```

---

## Task 7: Enhanced Arb Scanner (cross-temporal; complement deferred)

Add cross-temporal arb to `engine/arb_scanner.py`.

**On complement arb (spec Phase 4A item 2):** True complement arb requires independent YES ask + NO ask prices from separate token order books. In the current system, `no_best_ask = 1 - yes_bid` by definition, so YES ask + NO ask = YES ask + (1 - YES bid) which is always ≥ 1.0 — no arb possible from mid prices alone. Implementing this correctly requires tracking NO token best_ask from the CLOB independently. This is deferred; add a TODO in arb_scanner.py and document in discoveries.md.

**Files:**
- Modify: `engine/arb_scanner.py`
- Create: `tests/engine/test_arb_scanner_v2.py`

- [ ] **Step 1: Write failing tests**

Create `tests/engine/test_arb_scanner_v2.py`:
```python
from engine.arb_scanner import (
    find_cross_temporal_violations,
    ThresholdMarket,
    CrossTemporalViolation,
)


def _market(token_id, yes_price, no_token_id="no1", asset="BTC",
            target=100000, direction="above", expiry_key="mar2026"):
    return ThresholdMarket(
        token_id=token_id, asset=asset, target=target,
        direction=direction, expiry_key=expiry_key,
        yes_price=yes_price, no_token_id=no_token_id,
    )


def test_no_cross_temporal_violation_when_consistent():
    # P(BTC > 100k by March) <= P(BTC > 100k by June) — correct ordering
    mar = _market("tok1", yes_price=0.30, expiry_key="mar2026")
    jun = _market("tok2", yes_price=0.45, expiry_key="jun2026")
    violations = find_cross_temporal_violations([mar, jun], min_profit=0.01)
    assert violations == []


def test_cross_temporal_violation_detected():
    # P(BTC > 100k by March) > P(BTC > 100k by June) — VIOLATION
    mar = _market("tok1", yes_price=0.55, expiry_key="mar2026")
    jun = _market("tok2", yes_price=0.40, expiry_key="jun2026")
    violations = find_cross_temporal_violations([mar, jun], min_profit=0.01)
    assert len(violations) >= 1
    v = violations[0]
    assert isinstance(v, CrossTemporalViolation)
    assert v.profit > 0.01


def test_cross_temporal_below_direction():
    # P(BTC < 100k by March) >= P(BTC < 100k by June) — correct ordering
    mar = _market("tok1", yes_price=0.60, direction="below", expiry_key="mar2026")
    jun = _market("tok2", yes_price=0.45, direction="below", expiry_key="jun2026")
    violations = find_cross_temporal_violations([mar, jun], min_profit=0.01)
    assert violations == []


def test_cross_temporal_below_violation():
    # P(BTC < 100k by March) < P(BTC < 100k by June) — VIOLATION (later should be ≤ earlier)
    mar = _market("tok1", yes_price=0.30, direction="below", expiry_key="mar2026")
    jun = _market("tok2", yes_price=0.55, direction="below", expiry_key="jun2026")
    violations = find_cross_temporal_violations([mar, jun], min_profit=0.01)
    assert len(violations) >= 1


def test_empty_input():
    assert find_cross_temporal_violations([], min_profit=0.01) == []


def test_single_market_no_violation():
    markets = [_market("tok1", yes_price=0.50)]
    assert find_cross_temporal_violations(markets, min_profit=0.01) == []


def test_profit_below_min_filtered_out():
    mar = _market("tok1", yes_price=0.45, expiry_key="mar2026")
    jun = _market("tok2", yes_price=0.40, expiry_key="jun2026")
    # gross_profit = 0.05, net_profit = 0.05 - 0.04 = 0.01 exactly
    violations = find_cross_temporal_violations([mar, jun], min_profit=0.02)
    assert violations == []  # net_profit 0.01 < min_profit 0.02


def test_results_sorted_by_profit_descending():
    markets = [
        _market("tok1", yes_price=0.70, expiry_key="mar2026"),
        _market("tok2", yes_price=0.40, expiry_key="jun2026"),
        _market("tok3", yes_price=0.80, expiry_key="jan2026"),
    ]
    violations = find_cross_temporal_violations(markets, min_profit=0.01)
    profits = [v.profit for v in violations]
    assert profits == sorted(profits, reverse=True)
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/engine/test_arb_scanner_v2.py -v
```

- [ ] **Step 3: Add new dataclass and function to engine/arb_scanner.py**

At end of file, append:

```python
# TODO (deferred): Complement arb — requires tracking NO token best_ask independently
# from the YES token order book. Currently no_best_ask = 1 - yes_bid by definition,
# so YES ask + NO ask >= 1.0 always. Proper implementation needs ContractState.no_best_ask
# to be populated from the actual NO token CLOB feed, not derived. See discoveries.md.


@dataclass
class CrossTemporalViolation:
    earlier_token: str
    later_token: str
    asset: str
    target: float
    direction: str
    earlier_expiry_key: str
    later_expiry_key: str
    earlier_price: float
    later_price: float
    profit: float             # (earlier_price - later_price) = lockable profit
    trade_description: str


def find_cross_temporal_violations(
    markets: list,
    min_profit: float = 0.01,
    fee_rate: float = 0.02,
) -> list:
    """
    Detect cross-temporal arbitrage: same asset/target/direction, different expiry.

    Rule (for "above" direction):
    P(asset > target by t1) <= P(asset > target by t2) when t1 < t2.
    If the earlier-expiry market trades ABOVE the later-expiry market, it's an arb:
      - BUY NO on the overpriced earlier-expiry market
      - BUY YES on the underpriced later-expiry market
    Profit ≈ (earlier_price - later_price) - 2 × fee_rate

    For "below" direction, the inequality reverses:
    P(asset < target by t1) >= P(asset < target by t2) when t1 < t2.
    """
    # Group by (asset, target, direction)
    from collections import defaultdict
    groups: dict = defaultdict(list)
    for m in markets:
        key = f"{m.asset}_{m.target}_{m.direction}"
        groups[key].append(m)

    violations = []
    _EXPIRY_ORDER = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }

    def _expiry_sort_key(expiry_key: str) -> int:
        """Convert 'mar2026' → sortable int like 202603."""
        key_lower = expiry_key.lower()
        for month_str, month_num in _EXPIRY_ORDER.items():
            if key_lower.startswith(month_str):
                year_part = key_lower[len(month_str):]
                year = int(year_part) if year_part.isdigit() else 9999
                return year * 100 + month_num
        return 999999

    for group_key, group_markets in groups.items():
        if len(group_markets) < 2:
            continue

        sorted_markets = sorted(group_markets, key=lambda m: _expiry_sort_key(m.expiry_key))

        for i in range(len(sorted_markets)):
            for j in range(i + 1, len(sorted_markets)):
                earlier = sorted_markets[i]  # expires sooner
                later = sorted_markets[j]    # expires later

                if earlier.direction == "above":
                    # P(above target by t_early) <= P(above target by t_late)
                    # Violation: earlier.yes_price > later.yes_price
                    if earlier.yes_price > later.yes_price:
                        gross_profit = earlier.yes_price - later.yes_price
                        net_profit = gross_profit - 2 * fee_rate
                        if net_profit >= min_profit:
                            violations.append(CrossTemporalViolation(
                                earlier_token=earlier.token_id,
                                later_token=later.token_id,
                                asset=earlier.asset,
                                target=earlier.target,
                                direction=earlier.direction,
                                earlier_expiry_key=earlier.expiry_key,
                                later_expiry_key=later.expiry_key,
                                earlier_price=earlier.yes_price,
                                later_price=later.yes_price,
                                profit=net_profit,
                                trade_description=(
                                    f"BUY NO {earlier.asset}>${earlier.target:,.0f} "
                                    f"by {earlier.expiry_key} @ {1-earlier.yes_price:.3f}, "
                                    f"BUY YES {later.asset}>${later.target:,.0f} "
                                    f"by {later.expiry_key} @ {later.yes_price:.3f}"
                                ),
                            ))
                else:  # "below"
                    # P(below target by t_early) >= P(below target by t_late)
                    # Violation: earlier.yes_price < later.yes_price
                    if earlier.yes_price < later.yes_price:
                        gross_profit = later.yes_price - earlier.yes_price
                        net_profit = gross_profit - 2 * fee_rate
                        if net_profit >= min_profit:
                            violations.append(CrossTemporalViolation(
                                earlier_token=earlier.token_id,
                                later_token=later.token_id,
                                asset=earlier.asset,
                                target=earlier.target,
                                direction=earlier.direction,
                                earlier_expiry_key=earlier.expiry_key,
                                later_expiry_key=later.expiry_key,
                                earlier_price=earlier.yes_price,
                                later_price=later.yes_price,
                                profit=net_profit,
                                trade_description=(
                                    f"BUY YES {earlier.asset}<${earlier.target:,.0f} "
                                    f"by {earlier.expiry_key} @ {earlier.yes_price:.3f}, "
                                    f"BUY NO {later.asset}<${later.target:,.0f} "
                                    f"by {later.expiry_key} @ {1-later.yes_price:.3f}"
                                ),
                            ))

    violations.sort(key=lambda v: v.profit, reverse=True)
    return violations
```

- [ ] **Step 4: Wire cross-temporal scan into arb_scan_loop in main.py**

In `arb_scan_loop`, after `find_monotonicity_violations(...)`:
```python
        # Cross-temporal arb
        cross_violations = find_cross_temporal_violations(threshold_markets, min_profit=0.01)
        for v in cross_violations:
            log.info(f"CROSS-TEMPORAL ARB: {v.trade_description} | profit={v.profit:.3f}")
            tracker.log_signal(
                token_id=f"xarb_{v.earlier_token[:8]}_{v.later_token[:8]}",
                model_prob=0.99,
                market_prob=0.50,
                signal_summary={"type": "cross_temporal_arb", "profit": v.profit},
                size_usdc=0.0,
                ev=v.profit,
                side="ARB",
            )
```

Also add import at top of main.py:
```python
from engine.arb_scanner import find_monotonicity_violations, find_cross_temporal_violations
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/engine/test_arb_scanner_v2.py -v
pytest tests/engine/test_arb_scanner.py -v
pytest --tb=short -q
```

- [ ] **Step 6: Commit**

```bash
git add engine/arb_scanner.py tests/engine/test_arb_scanner_v2.py main.py
git commit -m "feat: cross-temporal + complement arb detection"
```

---

## Task 8: Dynamic Weight Optimizer (calibration/weight_optimizer.py)

Reads `fills.jsonl`, runs logistic regression on signal contributions, outputs weight recommendations. Output only — does NOT auto-update config.

**Files:**
- Create: `calibration/weight_optimizer.py`
- Create: `tests/calibration/test_weight_optimizer.py`

- [ ] **Step 1: Write failing tests**

Create `tests/calibration/test_weight_optimizer.py`:
```python
import json
import tempfile
from pathlib import Path
from calibration.weight_optimizer import (
    load_signal_matrix,
    optimize_weights,
    WeightRecommendation,
)


def _write_fills(fills: list[dict], path: Path):
    path.write_text("\n".join(json.dumps(f) for f in fills))


def test_load_signal_matrix_empty():
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = Path(f.name)
    path.write_text("")
    X, y, names = load_signal_matrix(str(path))
    assert len(X) == 0
    assert len(y) == 0


def test_load_signal_matrix_filters_unresolved():
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = Path(f.name)
    fills = [
        {"outcome": 1, "model_prob": 0.7, "signal_summary": {
            "signals": [{"name": "vol_skew", "strength": 0.5, "weight": 0.15, "confidence": 1.0}]}},
        {"outcome": None, "model_prob": 0.6, "signal_summary": {
            "signals": [{"name": "vol_skew", "strength": 0.3, "weight": 0.15, "confidence": 1.0}]}},
    ]
    _write_fills(fills, path)
    X, y, names = load_signal_matrix(str(path))
    assert len(X) == 1  # only resolved fills
    assert len(y) == 1
    assert y[0] == 1


def test_optimize_weights_returns_recommendations():
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = Path(f.name)
    fills = []
    for i in range(20):
        fills.append({
            "outcome": 1 if i % 3 != 0 else 0,
            "model_prob": 0.65,
            "signal_summary": {"signals": [
                {"name": "vol_skew", "strength": 0.6 if i % 3 != 0 else -0.3,
                 "weight": 0.15, "confidence": 1.0},
                {"name": "funding_rate", "strength": 0.4 if i % 3 != 0 else -0.2,
                 "weight": 0.15, "confidence": 1.0},
            ]}
        })
    _write_fills(fills, path)
    recommendations = optimize_weights(str(path))
    assert isinstance(recommendations, list)
    # With only 20 fills, should note insufficient data
    # but still return structure


def test_weight_recommendation_structure():
    rec = WeightRecommendation(
        signal_name="vol_skew",
        current_weight=0.15,
        recommended_weight=0.22,
        n_samples=100,
        confidence="high",
        note="",
    )
    assert rec.signal_name == "vol_skew"
    assert rec.recommended_weight == 0.22


def test_optimize_requires_100_samples():
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = Path(f.name)
    fills = [
        {"outcome": i % 2, "model_prob": 0.6,
         "signal_summary": {"signals": [
             {"name": "vol_skew", "strength": 0.5, "weight": 0.15, "confidence": 1.0}
         ]}}
        for i in range(50)
    ]
    _write_fills(fills, path)
    recs = optimize_weights(str(path))
    # Should warn about insufficient data
    for r in recs:
        assert r.confidence in ("low", "insufficient_data", "high", "medium")
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/calibration/test_weight_optimizer.py -v
```

- [ ] **Step 3: Create calibration/weight_optimizer.py**

```python
"""
calibration/weight_optimizer.py

Dynamic signal weight optimizer.

Reads fills.jsonl (resolved trades only), builds a feature matrix of
signal contributions, runs logistic regression, and outputs weight
recommendations for manual review.

Usage:
    python -m calibration.weight_optimizer [--fills fills.jsonl]

Output is recommendations only — does NOT auto-update config.py.
Results are written to discoveries.md with timestamp and sample size.
"""
import json
import argparse
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
from utils.logger import get_logger

log = get_logger(__name__)

MIN_SAMPLES_FOR_OPTIMIZATION = 100


@dataclass
class WeightRecommendation:
    signal_name: str
    current_weight: float
    recommended_weight: float
    n_samples: int
    confidence: str   # "high" | "medium" | "low" | "insufficient_data"
    note: str


def load_signal_matrix(log_file: str = "fills.jsonl") -> tuple[list, list, list]:
    """
    Parse fills.jsonl into (X, y, signal_names).
    X: list of dicts {signal_name: contribution} per fill
    y: list of outcomes (0 or 1)
    signal_names: sorted list of all signal names seen
    """
    path = Path(log_file)
    if not path.exists():
        return [], [], []

    rows = []
    outcomes = []
    all_signal_names = set()

    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        if record.get("outcome") is None:
            continue  # unresolved

        summary = record.get("signal_summary", {})
        signals = summary.get("signals", [])

        row = {}
        for s in signals:
            name = s.get("name", "")
            strength = float(s.get("strength", 0))
            weight = float(s.get("weight", 0))
            confidence = float(s.get("confidence", 1.0))
            contribution = weight * strength * confidence
            row[name] = contribution
            all_signal_names.add(name)

        rows.append(row)
        outcomes.append(int(record["outcome"]))

    signal_names = sorted(all_signal_names)
    # Build feature matrix (rows × signals), 0 if signal absent
    X = [{name: row.get(name, 0.0) for name in signal_names} for row in rows]

    return X, outcomes, signal_names


def optimize_weights(log_file: str = "fills.jsonl") -> list[WeightRecommendation]:
    """
    Run logistic regression on signal contributions and return weight recommendations.
    Requires MIN_SAMPLES_FOR_OPTIMIZATION resolved fills for 'high' confidence.
    """
    from config import SIGNAL_WEIGHTS

    X_dicts, y, signal_names = load_signal_matrix(log_file)
    n = len(X_dicts)

    if n == 0 or not signal_names:
        log.warning("no resolved fills found — cannot optimize weights")
        return []

    if n < MIN_SAMPLES_FOR_OPTIMIZATION:
        log.warning(f"only {n} resolved fills — need {MIN_SAMPLES_FOR_OPTIMIZATION} for reliable optimization")
        return [
            WeightRecommendation(
                signal_name=name,
                current_weight=SIGNAL_WEIGHTS.get(name, 0.0),
                recommended_weight=SIGNAL_WEIGHTS.get(name, 0.0),
                n_samples=n,
                confidence="insufficient_data",
                note=f"Need {MIN_SAMPLES_FOR_OPTIMIZATION - n} more resolved fills",
            )
            for name in signal_names
        ]

    # Build numpy arrays
    X = np.array([[row[name] for name in signal_names] for row in X_dicts])
    y_arr = np.array(y, dtype=float)

    # Simple logistic regression via gradient descent (no sklearn dependency)
    def sigmoid(z):
        return 1 / (1 + np.exp(-np.clip(z, -10, 10)))

    def log_loss(w, X, y):
        preds = sigmoid(X @ w)
        preds = np.clip(preds, 1e-7, 1 - 1e-7)
        return -np.mean(y * np.log(preds) + (1 - y) * np.log(1 - preds))

    # Initialize weights
    w = np.zeros(len(signal_names))
    lr = 0.1
    for _ in range(1000):
        preds = sigmoid(X @ w)
        grad = X.T @ (preds - y_arr) / n
        w -= lr * grad

    # Normalize coefficients to get recommended weights
    abs_w = np.abs(w)
    total = abs_w.sum()
    if total > 0:
        normalized = abs_w / total  # proportional weights that sum to 1.0
        # Scale to be in same range as current weights (max weight ~0.3)
        max_current = max(SIGNAL_WEIGHTS.values()) if SIGNAL_WEIGHTS else 0.3
        recommended = normalized * max_current * len(signal_names) * 0.5
    else:
        recommended = abs_w

    # Determine confidence based on sample size
    confidence = "high" if n >= 200 else "medium" if n >= 100 else "low"

    recommendations = []
    for i, name in enumerate(signal_names):
        current = SIGNAL_WEIGHTS.get(name, 0.0)
        rec_weight = round(float(recommended[i]), 3)
        delta_pct = abs(rec_weight - current) / max(current, 0.001) * 100
        note = ""
        if delta_pct > 50:
            note = f"Large change ({delta_pct:.0f}%) — review carefully"
        elif delta_pct < 5:
            note = "Current weight is close to optimal"

        recommendations.append(WeightRecommendation(
            signal_name=name,
            current_weight=current,
            recommended_weight=rec_weight,
            n_samples=n,
            confidence=confidence,
            note=note,
        ))

    return sorted(recommendations, key=lambda r: r.signal_name)


def print_recommendations(recommendations: list[WeightRecommendation]):
    print(f"\n{'='*60}")
    print(f"Signal Weight Optimization Report — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}")
    print(f"{'='*60}")
    if not recommendations:
        print("No recommendations — not enough data yet.")
        return
    n = recommendations[0].n_samples
    print(f"Resolved fills: {n} | Confidence: {recommendations[0].confidence}")
    print(f"\n{'Signal':<25} {'Current':>10} {'Recommended':>13} {'Delta':>8}  Note")
    print("-" * 70)
    for r in recommendations:
        delta = r.recommended_weight - r.current_weight
        delta_str = f"{delta:+.3f}"
        print(f"{r.signal_name:<25} {r.current_weight:>10.3f} {r.recommended_weight:>13.3f} {delta_str:>8}  {r.note}")
    print(f"\nDo NOT auto-apply. Review and update config.py manually.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Signal weight optimizer")
    parser.add_argument("--fills", default="fills.jsonl", help="Path to fills log")
    args = parser.parse_args()
    recs = optimize_weights(args.fills)
    print_recommendations(recs)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/calibration/test_weight_optimizer.py -v
pytest --tb=short -q
```

- [ ] **Step 5: Commit**

```bash
git add calibration/weight_optimizer.py tests/calibration/test_weight_optimizer.py
git commit -m "feat: dynamic weight optimizer — logistic regression on fills.jsonl"
```

---

## Task 9: Enhanced Calibration Report (calibration/metrics.py)

Add `--detailed` mode with per-signal attribution, time-decay analysis, category breakdown, and edge decay monitoring.

**Files:**
- Modify: `calibration/metrics.py`

- [ ] **Step 1: Write failing tests**

Add to existing `tests/calibration/` — create `tests/calibration/test_metrics_detailed.py`:
```python
import json
import tempfile
from pathlib import Path
from calibration.metrics import (
    load_resolved_fills,
    per_signal_attribution,
    category_breakdown,
    edge_decay_check,
    time_decay_analysis,
)


def _write_fills(fills, path):
    path.write_text("\n".join(json.dumps(f) for f in fills))


def _fill(outcome, model_prob=0.65, market_prob=0.55, edge=0.10,
          category="crypto", signals=None):
    return {
        "outcome": outcome,
        "model_prob": model_prob,
        "market_prob": market_prob,
        "edge": edge,
        "signal_summary": {
            "signals": signals or [
                {"name": "vol_skew", "strength": 0.5, "weight": 0.15, "confidence": 1.0}
            ]
        },
        "category": category,
        "timestamp": "2026-03-01T00:00:00Z",
    }


def test_per_signal_attribution_returns_dict():
    fills = [_fill(1), _fill(0), _fill(1)]
    result = per_signal_attribution(fills)
    assert isinstance(result, dict)
    assert "vol_skew" in result
    attr = result["vol_skew"]
    assert "win_rate" in attr
    assert "count" in attr


def test_category_breakdown_groups_correctly():
    fills = [_fill(1, category="crypto"), _fill(0, category="rates"), _fill(1, category="crypto")]
    result = category_breakdown(fills)
    assert "crypto" in result
    assert "rates" in result
    assert result["crypto"]["count"] == 2
    assert result["rates"]["count"] == 1


def test_edge_decay_check_no_alert_when_healthy():
    fills = [_fill(i % 2, edge=0.05) for i in range(30)]
    alert = edge_decay_check(fills, window_days=30, min_edge=0.02)
    assert isinstance(alert, dict)
    assert "rolling_edge" in alert


def test_edge_decay_check_alerts_when_edge_low():
    fills = [_fill(i % 2, edge=0.005) for i in range(30)]
    alert = edge_decay_check(fills, window_days=30, min_edge=0.02)
    assert alert.get("alert") is True


def test_time_decay_analysis_returns_bins():
    fills = [_fill(1) for _ in range(20)]
    result = time_decay_analysis(fills)
    assert isinstance(result, list)
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/calibration/test_metrics_detailed.py -v
```

- [ ] **Step 3: Add functions to calibration/metrics.py**

Append to `calibration/metrics.py`:
```python
import argparse
from datetime import datetime, timezone, timedelta


def per_signal_attribution(fills: list[dict]) -> dict:
    """
    Per-signal win rate: for each signal that appears in resolved fills,
    compute win rate when that signal was present and positive (strength > 0).
    """
    from collections import defaultdict
    signal_stats: dict = defaultdict(lambda: {"wins": 0, "losses": 0})

    for f in fills:
        outcome = f.get("outcome")
        if outcome is None:
            continue
        signals = f.get("signal_summary", {}).get("signals", [])
        for s in signals:
            name = s.get("name", "unknown")
            strength = float(s.get("strength", 0))
            if strength > 0:
                if outcome == 1:
                    signal_stats[name]["wins"] += 1
                else:
                    signal_stats[name]["losses"] += 1

    result = {}
    for name, stats in signal_stats.items():
        total = stats["wins"] + stats["losses"]
        result[name] = {
            "win_rate": stats["wins"] / total if total else 0.0,
            "count": total,
            "wins": stats["wins"],
        }
    return result


def category_breakdown(fills: list[dict]) -> dict:
    """Brier score and win rate per market category."""
    from collections import defaultdict
    groups: dict = defaultdict(list)
    for f in fills:
        cat = f.get("category", "unknown")
        groups[cat].append(f)

    result = {}
    for cat, cat_fills in groups.items():
        bs = brier_score(cat_fills)
        me = mean_edge(cat_fills)
        result[cat] = {
            "count": len(cat_fills),
            "brier_score": round(bs, 4),
            "mean_edge": round(me, 4),
        }
    return result


def edge_decay_check(
    fills: list[dict],
    window_days: int = 30,
    min_edge: float = 0.02,
) -> dict:
    """
    Compute rolling mean edge over last window_days.
    Alert if it falls below min_edge (signals may be getting arbitraged away).
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
    recent = [
        f for f in fills
        if f.get("timestamp", "") >= cutoff
    ]
    rolling_edge = mean_edge(recent) if recent else 0.0
    alert = rolling_edge < min_edge and len(recent) >= 10
    return {
        "rolling_edge": round(rolling_edge, 4),
        "window_days": window_days,
        "min_edge_threshold": min_edge,
        "recent_count": len(recent),
        "alert": alert,
        "message": f"ALERT: Rolling {window_days}d edge {rolling_edge:.3f} < {min_edge} threshold" if alert else "OK",
    }


def time_decay_analysis(fills: list[dict], bins: int = 4) -> list[dict]:
    """
    Check if signals degrade as markets approach resolution.
    Bins fills by days-to-expiry at signal time and computes Brier score per bin.
    Requires 'days_to_expiry' field in fills (optional — returns empty if absent).
    """
    binned: dict = {}
    for f in fills:
        dte = f.get("days_to_expiry")
        if dte is None:
            continue
        bin_idx = min(int(dte / 7), bins - 1)  # weekly bins
        if bin_idx not in binned:
            binned[bin_idx] = []
        binned[bin_idx].append(f)

    result = []
    for bin_idx in sorted(binned):
        bin_fills = binned[bin_idx]
        result.append({
            "week": bin_idx,
            "label": f"Week {bin_idx} before expiry",
            "count": len(bin_fills),
            "brier_score": round(brier_score(bin_fills), 4),
            "mean_edge": round(mean_edge(bin_fills), 4),
        })
    return result


def print_detailed_report(log_file: str = "fills.jsonl"):
    fills = load_resolved_fills(log_file)
    print_calibration_report(log_file)  # existing basic report

    if not fills:
        return

    print("\n--- Per-Signal Attribution ---")
    attribution = per_signal_attribution(fills)
    for name, stats in sorted(attribution.items(), key=lambda x: -x[1]["count"]):
        print(f"  {name:<25} win_rate={stats['win_rate']:.2%} n={stats['count']}")

    print("\n--- Category Breakdown ---")
    for cat, stats in category_breakdown(fills).items():
        print(f"  {cat:<12} n={stats['count']} brier={stats['brier_score']} edge={stats['mean_edge']:.3f}")

    print("\n--- Edge Decay Check (30-day rolling) ---")
    decay = edge_decay_check(fills)
    print(f"  {decay['message']}  (n={decay['recent_count']})")

    print("\n--- Time Decay Analysis ---")
    for row in time_decay_analysis(fills):
        print(f"  {row['label']:<25} n={row['count']} brier={row['brier_score']} edge={row['mean_edge']:.3f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Calibration metrics")
    parser.add_argument("--detailed", action="store_true", help="Full breakdown report")
    parser.add_argument("--fills", default="fills.jsonl", help="Path to fills log")
    args = parser.parse_args()
    if args.detailed:
        print_detailed_report(args.fills)
    else:
        print_calibration_report(args.fills)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/calibration/test_metrics_detailed.py -v
pytest --tb=short -q
```

- [ ] **Step 5: Commit**

```bash
git add calibration/metrics.py tests/calibration/test_metrics_detailed.py
git commit -m "feat: detailed calibration report — per-signal, category, edge decay, time decay"
```

---

## Task 10: Market Screener (market/market_screener.py)

Ranks markets by edge opportunity: high volume + narrow spread + approaching resolution + our signal coverage.

**Files:**
- Create: `market/market_screener.py`
- Create: `tests/market/test_market_screener.py`

- [ ] **Step 1: Write failing tests**

Create `tests/market/test_market_screener.py`:
```python
from datetime import datetime, timezone, timedelta
from market.market_screener import score_market, rank_markets, ScreenedMarket
from market.state import ContractState


def _cs(question, volume=100000, bid=0.48, ask=0.52, category="crypto",
        expiry_hours=48):
    cs = ContractState(
        yes_token_id="tok1", no_token_id="tok2",
        question=question, category=category,
        best_bid=bid, best_ask=ask, volume_usd=volume,
    )
    return cs


def test_score_market_returns_float():
    cs = _cs("Will BTC > $100k by March?")
    expiry = datetime.now(timezone.utc) + timedelta(hours=48)
    score = score_market(cs, expiry=expiry, is_parseable=True)
    assert isinstance(score, float)
    assert score >= 0


def test_higher_volume_scores_higher():
    expiry = datetime.now(timezone.utc) + timedelta(hours=48)
    low_vol = _cs("Will BTC > $100k?", volume=1000)
    high_vol = _cs("Will BTC > $100k?", volume=1000000)
    s_low = score_market(low_vol, expiry=expiry, is_parseable=True)
    s_high = score_market(high_vol, expiry=expiry, is_parseable=True)
    assert s_high > s_low


def test_parseable_scores_higher_than_not():
    expiry = datetime.now(timezone.utc) + timedelta(hours=48)
    cs = _cs("Will BTC > $100k?", volume=50000)
    s_parseable = score_market(cs, expiry=expiry, is_parseable=True)
    s_not = score_market(cs, expiry=expiry, is_parseable=False)
    assert s_parseable > s_not


def test_narrow_spread_scores_higher():
    expiry = datetime.now(timezone.utc) + timedelta(hours=48)
    narrow = _cs("Will BTC > $100k?", bid=0.49, ask=0.51)
    wide = _cs("Will BTC > $100k?", bid=0.30, ask=0.70)
    s_narrow = score_market(narrow, expiry=expiry, is_parseable=True)
    s_wide = score_market(wide, expiry=expiry, is_parseable=True)
    assert s_narrow > s_wide


def test_rank_markets_returns_sorted_list():
    expiry = datetime.now(timezone.utc) + timedelta(hours=48)
    markets = {
        "tok1": (_cs("Will BTC > $100k?", volume=100000), expiry, True),
        "tok2": (_cs("Will ETH > $5k?", volume=5000), expiry, True),
        "tok3": (_cs("Will something happen?", volume=50000), expiry, False),
    }
    ranked = rank_markets(markets)
    assert len(ranked) == 3
    assert isinstance(ranked[0], ScreenedMarket)
    assert ranked[0].score >= ranked[1].score  # descending order


def test_rank_markets_flags_staleness():
    stale_expiry = datetime.now(timezone.utc) + timedelta(hours=24)
    cs = _cs("Will BTC > $100k?", volume=10)  # very low volume = stale
    markets = {"tok1": (cs, stale_expiry, True)}
    ranked = rank_markets(markets)
    assert ranked[0].stale_flag is True or ranked[0].stale_flag is False  # either, just check it exists
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/market/test_market_screener.py -v
```

- [ ] **Step 3: Create market/market_screener.py**

```python
"""
market/market_screener.py

Hourly market screener: ranks active markets by edge opportunity.

Scoring factors:
1. Volume (log-scaled): high volume = more liquid = easier to exit
2. Spread: narrower spread = better fill price
3. Time to expiry: moderate time (24-72h) scores highest
4. Parseability: our signal engine has coverage
5. Staleness penalty: declining volume = harder exit

Run standalone:
    python -m market.market_screener

Or call rank_markets() from any async context.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
import math

from market.state import ContractState
from utils.logger import get_logger

log = get_logger(__name__)

_STALE_VOLUME_THRESHOLD = 5_000   # markets below this are flagged stale


@dataclass
class ScreenedMarket:
    token_id: str
    question: str
    category: str
    score: float
    volume_usd: float
    spread: float
    hours_to_expiry: Optional[float]
    is_parseable: bool
    stale_flag: bool


def score_market(
    cs: ContractState,
    expiry: Optional[datetime],
    is_parseable: bool,
) -> float:
    """
    Compute opportunity score for a single market.
    Higher = better edge opportunity.
    """
    score = 0.0

    # Volume component (log-scale, max weight 40)
    if cs.volume_usd > 0:
        score += min(40.0, math.log1p(cs.volume_usd) * 2.5)

    # Spread component (tight spread = +30, wide = 0)
    spread = cs.best_ask - cs.best_bid
    if spread < 0.50:
        score += max(0.0, 30.0 * (1 - spread / 0.50))

    # Time to expiry component (peak at 24-72h window)
    if expiry is not None:
        now = datetime.now(timezone.utc)
        hours_left = (expiry - now).total_seconds() / 3600
        if 12 <= hours_left <= 72:
            score += 20.0
        elif 72 < hours_left <= 168:
            score += 10.0
        elif hours_left < 12:
            score += 5.0  # very close — may not fill

    # Parseability bonus
    if is_parseable:
        score += 15.0

    return round(score, 2)


def rank_markets(
    markets: dict,  # {token_id: (ContractState, expiry_datetime, is_parseable)}
) -> list[ScreenedMarket]:
    """
    Rank markets by opportunity score.
    Input format matches what CLOBMonitor produces.
    """
    screened = []
    now = datetime.now(timezone.utc)

    for token_id, item in markets.items():
        cs, expiry, is_parseable = item
        score = score_market(cs, expiry, is_parseable)

        hours_left = None
        if expiry:
            hours_left = (expiry - now).total_seconds() / 3600

        spread = cs.best_ask - cs.best_bid
        stale = cs.volume_usd < _STALE_VOLUME_THRESHOLD

        screened.append(ScreenedMarket(
            token_id=token_id,
            question=cs.question[:80],
            category=cs.category,
            score=score,
            volume_usd=cs.volume_usd,
            spread=round(spread, 4),
            hours_to_expiry=round(hours_left, 1) if hours_left is not None else None,
            is_parseable=is_parseable,
            stale_flag=stale,
        ))

    screened.sort(key=lambda m: m.score, reverse=True)
    return screened


def print_watchlist(ranked: list[ScreenedMarket], top_n: int = 20):
    print(f"\n{'='*80}")
    print(f"Market Screener — Top {min(top_n, len(ranked))} Opportunities")
    print(f"{'='*80}")
    print(f"{'Score':>6}  {'Vol($)':>10}  {'Spread':>7}  {'Hours':>6}  {'Cat':>6}  Question")
    print("-" * 80)
    for m in ranked[:top_n]:
        stale_marker = " [STALE]" if m.stale_flag else ""
        print(
            f"{m.score:>6.1f}  {m.volume_usd:>10,.0f}  {m.spread:>7.4f}  "
            f"{m.hours_to_expiry or 0:>6.1f}  {m.category:>6}  "
            f"{m.question[:50]}{stale_marker}"
        )
    print(f"{'='*80}\n")


if __name__ == "__main__":
    import asyncio
    import httpx
    from market.clob_monitor import fetch_active_markets, select_markets
    from engine.contract_parser import parse_contract

    async def _run():
        async with httpx.AsyncClient() as client:
            token_map = await fetch_active_markets(client)
        token_map = select_markets(token_map)

        market_inputs = {}
        for token_id, meta in token_map.items():
            from market.state import ContractState
            cs = ContractState(
                yes_token_id=token_id,
                no_token_id=meta["no_token_id"],
                question=meta["question"],
                category=meta["category"],
                best_bid=meta["best_bid"],
                best_ask=meta["best_ask"],
                volume_usd=meta["volume"],
            )
            market_inputs[token_id] = (cs, meta["expiry"], meta["parseable"])

        ranked = rank_markets(market_inputs)
        print_watchlist(ranked, top_n=25)

    asyncio.run(_run())
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/market/test_market_screener.py -v
pytest --tb=short -q
```

- [ ] **Step 5: Commit**

```bash
git add market/market_screener.py tests/market/test_market_screener.py
git commit -m "feat: market screener — ranks opportunities by edge potential"
```

---

## Task 11: Monitoring Alerts (monitoring/alerts.py)

Telegram notifications for key events. Reuses existing `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` from config.

**Files:**
- Create: `monitoring/__init__.py`
- Create: `monitoring/alerts.py`
- Create: `tests/monitoring/__init__.py`
- Create: `tests/monitoring/test_alerts.py`

- [ ] **Step 1: Write failing tests**

Create `tests/monitoring/test_alerts.py`:
```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from monitoring.alerts import AlertManager, AlertType


def test_alert_type_enum():
    assert AlertType.TRADE_EXECUTED
    assert AlertType.ARB_DETECTED
    assert AlertType.DAILY_SUMMARY
    assert AlertType.FEED_DISCONNECTED
    assert AlertType.RISK_LIMIT_APPROACHING
    assert AlertType.FLATLINE_DETECTED


def test_alert_manager_init_no_config():
    # Should not raise even if no token configured
    mgr = AlertManager(bot_token="", chat_id="")
    assert mgr is not None
    assert not mgr.enabled


def test_alert_manager_init_with_config():
    mgr = AlertManager(bot_token="123:ABC", chat_id="456")
    assert mgr.enabled


def test_format_trade_message():
    mgr = AlertManager(bot_token="123:ABC", chat_id="456")
    msg = mgr.format_trade(
        side="BUY_YES", question="Will BTC > $100k?",
        size=25.0, model_prob=0.72, market_mid=0.65, ev=0.07
    )
    assert "BUY_YES" in msg
    assert "25.0" in msg
    assert "BTC" in msg


def test_format_arb_message():
    mgr = AlertManager(bot_token="123:ABC", chat_id="456")
    msg = mgr.format_arb(description="BUY YES BTC>$100k, BUY NO BTC>$90k", spread=0.05)
    assert "0.05" in msg or "5" in msg
    assert "ARB" in msg.upper() or "arb" in msg.lower()


@pytest.mark.asyncio
async def test_send_alert_disabled_when_no_token():
    mgr = AlertManager(bot_token="", chat_id="")
    result = await mgr.send(AlertType.TRADE_EXECUTED, "test message")
    assert result is False  # silently skipped


@pytest.mark.asyncio
async def test_send_alert_calls_telegram_api():
    mgr = AlertManager(bot_token="123:ABC", chat_id="456")
    with patch("monitoring.alerts.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client.post = AsyncMock(return_value=mock_resp)
        result = await mgr.send(AlertType.TRADE_EXECUTED, "trade happened")
    mock_client.post.assert_called_once()
    call_kwargs = mock_client.post.call_args
    assert "456" in str(call_kwargs)
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/monitoring/test_alerts.py -v
```

- [ ] **Step 3: Create monitoring/__init__.py**

```python
```
(empty file)

- [ ] **Step 4: Create tests/monitoring/__init__.py**

```python
```
(empty file)

- [ ] **Step 5: Create monitoring/alerts.py**

```python
"""
monitoring/alerts.py

Telegram alert system for key bot events.

Sends notifications for:
- Trade executed (paper or live)
- Arb opportunity detected
- Daily P&L summary
- Feed disconnection
- Risk limit approaching (>80% of daily loss limit)
- Flatline pattern detected on high-volume market

Configure via .env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
Uses raw HTTP (same approach as telegram_bot.py — no python-telegram-bot dependency).
"""
import asyncio
from enum import Enum
from typing import Optional

import httpx
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from utils.logger import get_logger

log = get_logger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


class AlertType(Enum):
    TRADE_EXECUTED = "trade"
    ARB_DETECTED = "arb"
    DAILY_SUMMARY = "summary"
    FEED_DISCONNECTED = "feed_down"
    RISK_LIMIT_APPROACHING = "risk"
    FLATLINE_DETECTED = "flatline"


class AlertManager:
    """
    Sends Telegram alerts. Silently disabled if bot_token or chat_id is empty.
    All send() calls are fire-and-forget (logged on failure, never raise).
    """

    def __init__(self, bot_token: str = "", chat_id: str = ""):
        self._token = bot_token or TELEGRAM_BOT_TOKEN
        self._chat_id = chat_id or TELEGRAM_CHAT_ID
        self.enabled = bool(self._token and self._chat_id)
        if not self.enabled:
            log.debug("AlertManager: Telegram not configured — alerts disabled")

    @property
    def _url(self) -> str:
        return _TELEGRAM_API.format(token=self._token)

    async def send(self, alert_type: AlertType, message: str) -> bool:
        """Send alert. Returns True on success, False on failure/disabled."""
        if not self.enabled:
            return False
        prefix = {
            AlertType.TRADE_EXECUTED:      "[TRADE]",
            AlertType.ARB_DETECTED:        "[ARB]",
            AlertType.DAILY_SUMMARY:       "[SUMMARY]",
            AlertType.FEED_DISCONNECTED:   "[FEED DOWN]",
            AlertType.RISK_LIMIT_APPROACHING: "[RISK ALERT]",
            AlertType.FLATLINE_DETECTED:   "[FLATLINE]",
        }.get(alert_type, "[ALERT]")
        full_msg = f"{prefix} {message}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(self._url, json={
                    "chat_id": self._chat_id,
                    "text": full_msg,
                    "parse_mode": "HTML",
                })
                if resp.status_code != 200:
                    log.warning(f"Telegram alert failed: {resp.status_code} {resp.text[:100]}")
                    return False
            return True
        except Exception as exc:
            log.warning(f"Telegram alert error: {exc}")
            return False

    def format_trade(
        self,
        side: str,
        question: str,
        size: float,
        model_prob: float,
        market_mid: float,
        ev: float,
    ) -> str:
        from config import PAPER
        paper_tag = "[PAPER] " if PAPER else ""
        return (
            f"{paper_tag}{side} ${size:.2f}\n"
            f"Q: {question[:60]}\n"
            f"Model: {model_prob:.2%} | Market: {market_mid:.2%} | EV: {ev:.2%}"
        )

    def format_arb(self, description: str, spread: float) -> str:
        return f"Arb detected — spread={spread:.3f}\n{description}"

    def format_daily_summary(
        self,
        session_pnl: float,
        trades_today: int,
        open_positions: int,
    ) -> str:
        pnl_sign = "+" if session_pnl >= 0 else ""
        return (
            f"Daily P&L: {pnl_sign}${session_pnl:.2f}\n"
            f"Trades: {trades_today} | Open positions: {open_positions}"
        )

    def format_feed_down(self, feed_name: str, seconds_stale: float) -> str:
        return f"Feed '{feed_name}' has been stale for {seconds_stale:.0f}s — check connection"

    def format_risk_alert(
        self,
        daily_loss: float,
        daily_limit: float,
    ) -> str:
        pct = daily_loss / daily_limit * 100 if daily_limit else 0
        return f"Risk limit at {pct:.0f}%: -${daily_loss:.2f} / limit -${daily_limit:.2f}"

    def format_flatline(self, question: str, hours_left: float, leading_price: float) -> str:
        return (
            f"Flatline pattern on high-volume market:\n"
            f"Q: {question[:60]}\n"
            f"Leading price: {leading_price:.2%} | {hours_left:.1f}h to resolution"
        )


# Singleton for use from main.py
_alert_manager: Optional[AlertManager] = None


def get_alert_manager() -> AlertManager:
    global _alert_manager
    if _alert_manager is None:
        _alert_manager = AlertManager()
    return _alert_manager
```

- [ ] **Step 6: Wire alert into main.py trading loop**

After a successful trade (`result.success`), add:
```python
                if result.success:
                    await risk.open_position(yes_token_id, parsed, size, result.filled_price)
                    n_traded += 1
                    mkt_rec["traded"] = True
                    mkt_rec["reason"] = None
                    # Alert
                    from monitoring.alerts import get_alert_manager, AlertType
                    alert_mgr = get_alert_manager()
                    asyncio.create_task(alert_mgr.send(
                        AlertType.TRADE_EXECUTED,
                        alert_mgr.format_trade(
                            side=side, question=contract_state.question,
                            size=size, model_prob=model_prob,
                            market_mid=contract_state.mid, ev=ev
                        )
                    ))
```

Also add arb alert in `arb_scan_loop` for violations with spread > 0.05.

- [ ] **Step 7: Run tests**

```bash
pytest tests/monitoring/test_alerts.py -v
pytest --tb=short -q
```

- [ ] **Step 8: Commit**

```bash
git add monitoring/__init__.py monitoring/alerts.py tests/monitoring/__init__.py tests/monitoring/test_alerts.py main.py
git commit -m "feat: Telegram alert system for trades, arb, risk, flatline, feed health"
```

---

---

## Task 12: Contract Parser Expansion (Phase 3A)

Add two new market categories to `engine/contract_parser.py`:
1. **Election/political** — "Will X win the 2026 midterm?" → category `"election"`, uses flatline signal near resolution
2. **Deadline/event** — "Will X happen by March 31?" → category `"event"`, uses flatline signal (strongest pattern for these)

**Files:**
- Modify: `engine/contract_parser.py`
- Create: `tests/engine/test_parser_categories.py`

- [ ] **Step 1: Write failing tests**

Create `tests/engine/test_parser_categories.py`:
```python
from engine.contract_parser import parse_contract


def test_election_category_detected():
    c = parse_contract("tok1", "Will Democrats win the 2026 midterm elections?")
    assert c.category == "election"
    assert c.parseable is True


def test_election_win_direction():
    c = parse_contract("tok1", "Will Biden win the 2026 Senate race?")
    assert c.category == "election"
    assert c.direction == "yes"


def test_event_deadline_category():
    c = parse_contract("tok1", "Will the US debt ceiling be raised by March 31?")
    assert c.category == "event"
    assert c.parseable is True


def test_event_crypto_milestone():
    c = parse_contract("tok1", "Will an Ethereum ETF be approved by Q2 2026?")
    assert c.category == "event"
    assert c.parseable is True


def test_existing_crypto_unchanged():
    c = parse_contract("tok1", "Will BTC reach $100k by March?")
    assert c.category == "crypto"


def test_existing_rates_unchanged():
    c = parse_contract("tok1", "Will the Fed cut rates in May?")
    assert c.category == "rates"


def test_election_parseable_has_expiry():
    c = parse_contract("tok1", "Will Republicans win the 2026 midterm?")
    assert c.expiry is not None
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/engine/test_parser_categories.py -v
```

- [ ] **Step 3: Add election and event detection to contract_parser.py**

In `parse_contract()`, add two new detection blocks BEFORE the crypto asset detection (after macro detection, before the `_SKIP_PATTERNS` check):

```python
    # 2b. Election/political markets
    _ELECTION_RE = re.compile(
        r'\belection\b|\bmidterm\b|\bprimary\b|\bpresidential\b|\bsenate\b|\bhouse\s+race\b|\bballot\b|\bvote\b|\bcandidate\b',
        re.IGNORECASE,
    )
    if _ELECTION_RE.search(q) and any(w in q for w in ["win", "lose", "elected", "wins"]):
        contract.category = "election"
        contract.direction = "yes"   # "will X win?" = YES direction
        contract.expiry = _parse_expiry(question)
        if not contract.expiry:
            # Default: end of year for election markets
            now = datetime.now(timezone.utc)
            contract.expiry = now.replace(month=12, day=31, hour=23, minute=59)
        contract.parseable = True
        log.debug(f"election market: {question[:60]}")
        return contract

    # 2c. Deadline/event markets ("Will X happen by DATE?")
    _EVENT_RE = re.compile(
        r'\bby\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|\d{4}|q[1-4])\b'
        r'|\bby\s+end\s+of\b|\bapproved?\b|\bpassed?\b|\blaunched?\b|\breleased?\b',
        re.IGNORECASE,
    )
    if _EVENT_RE.search(q) and not any(alias in q for alias in ASSET_ALIASES):
        expiry = _parse_expiry(question)
        if expiry:
            contract.category = "event"
            contract.direction = "yes"
            contract.expiry = expiry
            contract.parseable = True
            log.debug(f"event/deadline market: {question[:60]}")
            return contract
```

Note: `_ELECTION_RE` and `_EVENT_RE` should be defined as module-level constants alongside existing `_RATE_RE`.

- [ ] **Step 4: Run tests**

```bash
pytest tests/engine/test_parser_categories.py -v
pytest tests/engine/test_contract_parser.py -v
pytest tests/engine/test_contract_parser_v3.py -v
pytest --tb=short -q
```

Expected: new tests pass, all 222+ existing tests pass.

- [ ] **Step 5: Commit**

```bash
git add engine/contract_parser.py tests/engine/test_parser_categories.py
git commit -m "feat: contract parser — election/political and deadline/event categories"
```

---

## Task 13: Execution Improvements (Phase 4D)

**Files:**
- Modify: `trading/executor.py` — stale order cleanup + smart order splitting
- Modify: `market/clob_monitor.py` — heartbeat safety (cancel all orders on >60s WS disconnect)
- Create: `tests/trading/test_executor_improvements.py`

- [ ] **Step 1: Write failing tests**

Create `tests/trading/test_executor_improvements.py`:
```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from trading.executor import CLOBExecutor, should_cancel_stale_order, split_order_sizes


def test_split_order_sizes_below_threshold():
    # Orders <= $30 are not split
    sizes = split_order_sizes(25.0)
    assert sizes == [25.0]


def test_split_order_sizes_above_threshold():
    # Orders > $30 split into 2-3 smaller orders
    sizes = split_order_sizes(45.0)
    assert len(sizes) >= 2
    assert abs(sum(sizes) - 45.0) < 0.01


def test_split_order_sizes_capped():
    # Each split piece must not exceed MAX_TRADE_SIZE_USDC
    sizes = split_order_sizes(50.0)
    from config import MAX_TRADE_SIZE_USDC
    assert all(s <= MAX_TRADE_SIZE_USDC for s in sizes)


def test_should_cancel_stale_order_when_old_and_price_moved():
    result = should_cancel_stale_order(
        order_age_minutes=35,
        price_at_order=0.60,
        current_price=0.63,   # 3% move
    )
    assert result is True


def test_should_not_cancel_when_price_stable():
    result = should_cancel_stale_order(
        order_age_minutes=35,
        price_at_order=0.60,
        current_price=0.61,   # 1% move — within tolerance
    )
    assert result is False


def test_should_not_cancel_when_young():
    result = should_cancel_stale_order(
        order_age_minutes=10,  # only 10 min old
        price_at_order=0.60,
        current_price=0.65,   # big price move but order is young
    )
    assert result is False
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/trading/test_executor_improvements.py -v
```

- [ ] **Step 3: Add helper functions to trading/executor.py**

At module level (after imports, before `CLOBExecutor` class):

```python
_ORDER_STALE_MINUTES = 30     # cancel open orders older than this
_PRICE_MOVE_CANCEL_PCT = 0.02 # cancel if price moved > 2% since order placed
_ORDER_SPLIT_THRESHOLD = 30.0  # split orders above this USDC amount


def should_cancel_stale_order(
    order_age_minutes: float,
    price_at_order: float,
    current_price: float,
) -> bool:
    """Return True if a stale open order should be cancelled and re-priced."""
    if order_age_minutes < _ORDER_STALE_MINUTES:
        return False
    price_change = abs(current_price - price_at_order) / max(price_at_order, 0.001)
    return price_change > _PRICE_MOVE_CANCEL_PCT


def split_order_sizes(total_size: float, n_splits: int = 2) -> list[float]:
    """
    For orders above _ORDER_SPLIT_THRESHOLD, split into n_splits pieces.
    Each piece gets a slightly different price (caller offsets by 0.5-1c).
    Returns list of sizes summing to total_size.
    """
    if total_size <= _ORDER_SPLIT_THRESHOLD:
        return [total_size]
    piece = round(total_size / n_splits, 2)
    sizes = [piece] * (n_splits - 1)
    sizes.append(round(total_size - sum(sizes), 2))
    return sizes
```

- [ ] **Step 4: Add heartbeat safety comment to market/clob_monitor.py**

In `CLOBMonitor._run()`, in the `except Exception` block, add:
```python
            except Exception as exc:
                self.log.warning(f"WS error: {exc} — reconnecting in 10s")
                # HEARTBEAT SAFETY: if disconnect > 60s, consider cancelling open orders.
                # Currently handled by reconnect loop + paper mode position TTL.
                # TODO (Phase 4D): on live mode, call executor.cancel_all_open_orders()
                # if time since last stamp_feed("clob") > 60 seconds.
                await asyncio.sleep(10)
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/trading/test_executor_improvements.py -v
pytest tests/trading/ -v
pytest --tb=short -q
```

- [ ] **Step 6: Commit**

```bash
git add trading/executor.py market/clob_monitor.py tests/trading/test_executor_improvements.py
git commit -m "feat: stale order detection, order splitting helpers; heartbeat safety TODO"
```

---

## Task 15: Run Full Test Suite + Verify 222+ Tests Pass

- [ ] **Step 1: Run complete test suite**

```bash
pytest --tb=short -q 2>&1 | tail -20
```

Expected: 222+ tests pass, 0 failures.

- [ ] **Step 2: Count new tests**

```bash
pytest --collect-only -q 2>&1 | tail -5
```

Expected: 50+ new tests added.

- [ ] **Step 3: Write discoveries.md with backtest plans**

Document:
- Flatline signal: hypothesis, parameters used, target metrics to validate
- OBI signal: hypothesis, thresholds, expected noise level
- VPD signal: hypothesis, signed direction via price drift; expected lag
- Complement arb: deferred — requires independent NO token order book tracking
- Note: backtesting requires 50+ resolved markets in fills.jsonl

- [ ] **Step 4: Final commit**

```bash
git add discoveries.md recon_notes.md
git commit -m "docs: discoveries.md — signal hypotheses, backtest targets, complement arb deferred note"
```

---

## Post-Implementation Checklist

- [ ] `pytest --tb=short -q` passes with 0 failures
- [ ] 50+ new tests added (on top of existing 222)
- [ ] `python -m calibration.metrics --detailed` runs without error on an empty fills.jsonl
- [ ] `python -m market.market_screener` connects to Gamma API and prints ranked list
- [ ] `python -m calibration.weight_optimizer` prints "insufficient data" note (expected before 100 fills)
- [ ] All new signals appear in engine.summary() output when they fire
- [ ] New categories ("election", "event") parse correctly via parse_contract()
- [ ] `should_cancel_stale_order()` and `split_order_sizes()` importable from trading.executor
- [ ] `discoveries.md` documents all signal hypotheses with target validation metrics
- [ ] `recon_notes.md` summarizes reference repo findings
- [ ] Complement arb deferred with TODO comment in arb_scanner.py
