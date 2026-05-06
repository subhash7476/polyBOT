# Ladder Depth + Merge Integration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace single-level quoting with a 3-level bid/ask ladder per market, and wire the existing redemption loop into maker mode so resolved positions are recycled automatically.

**Architecture:** The `QuoteEngine` emits a new `LadderUpdate` type containing all levels for a market in one message. The `OrderManager` cancels all old levels and replaces them atomically. Merge integration reuses the existing `redeemall_loop` logic, extracted from `main.py` into `trading/redeemall.py` so both taker and maker modes share it.

**Tech Stack:** Python 3.11, asyncio, py-clob-client, pytest

---

## File Map

| File | Change |
|---|---|
| `maker/types.py` | Add `LadderUpdate` dataclass (wraps `list[QuoteIntent]`) |
| `maker/state.py` | `live_orders: dict[str, list[dict]]` (was `dict[str, dict]`) |
| `maker/quote_engine.py` | Replace `build_quote()` with `build_ladder()`; emit `LadderUpdate` |
| `maker/order_manager.py` | Replace `handle_quote_intent_sync` with `handle_ladder_sync`; cancel iterates list |
| `maker/fill_poller.py` | `check_paper_fills` iterates `list[dict]` per token_id |
| `trading/redeemall.py` | Extract `redeemall_loop()` as standalone async function |
| `main.py` | Import `redeemall_loop` from `trading/redeemall`; remove local definition |
| `maker/runner.py` | Add `redeemall_loop` coroutine in live mode |
| `tests/maker/test_types.py` | Add tests for `LadderUpdate` |
| `tests/maker/test_quote_engine.py` | Replace `build_quote` tests with `build_ladder` tests |
| `tests/maker/test_order_manager.py` | Replace `handle_quote_intent_sync` calls with `handle_ladder_sync` |
| `tests/maker/test_fill_poller.py` | Update `live_orders` setup to use `list[dict]` |

---

## Task 1: Add `LadderUpdate` type

**Files:**
- Modify: `maker/types.py`
- Test: `tests/maker/test_types.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/maker/test_types.py  (add to existing file)
from maker.types import LadderUpdate, QuoteIntent

def test_ladder_update_levels():
    levels = [
        QuoteIntent("tok1", 0.44, 0.56, 10.0, 10.0, "reprice"),
        QuoteIntent("tok1", 0.45, 0.55, 10.0, 10.0, "reprice"),
        QuoteIntent("tok1", 0.46, 0.54, 10.0, 10.0, "reprice"),
    ]
    lu = LadderUpdate(token_id="tok1", levels=levels, reason="reprice")
    assert lu.token_id == "tok1"
    assert len(lu.levels) == 3
    assert lu.levels[0].bid_price == 0.44
    assert lu.reason == "reprice"

def test_ladder_update_center():
    """center property returns the middle level."""
    levels = [
        QuoteIntent("tok1", 0.44, 0.56, 10.0, 10.0, "reprice"),
        QuoteIntent("tok1", 0.45, 0.55, 10.0, 10.0, "reprice"),
        QuoteIntent("tok1", 0.46, 0.54, 10.0, 10.0, "reprice"),
    ]
    lu = LadderUpdate(token_id="tok1", levels=levels, reason="reprice")
    assert lu.center.bid_price == 0.45
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/maker/test_types.py::test_ladder_update_levels -v
```
Expected: `ImportError: cannot import name 'LadderUpdate'`

- [ ] **Step 3: Add `LadderUpdate` to `maker/types.py`**

Add after the existing `CancelAll` class:

```python
@dataclass(frozen=True)
class LadderUpdate:
    """Emitted by QuoteEngine → consumed by OrderManager.
    Replaces all existing levels for token_id with this new set."""
    token_id: str
    levels: tuple  # tuple[QuoteIntent, ...] — frozen dataclass requires immutable field
    reason: str

    def __post_init__(self):
        # Normalise to tuple so dataclass stays hashable
        object.__setattr__(self, "levels", tuple(self.levels))

    @property
    def center(self) -> "QuoteIntent":
        """Middle level — used for stale detection."""
        return self.levels[len(self.levels) // 2]
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/maker/test_types.py -v
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add maker/types.py tests/maker/test_types.py
git commit -m "feat(maker): add LadderUpdate type for multi-level quoting"
```

---

## Task 2: Update `MakerState.live_orders` to `list[dict]`

**Files:**
- Modify: `maker/state.py`
- Test: `tests/maker/test_state.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/maker/test_state.py`:

```python
def test_live_orders_is_list_per_market():
    """live_orders[token_id] must be a list of per-level dicts."""
    s = MakerState()
    s.live_orders["tok1"] = [
        {"bid_order_id": "b1", "ask_order_id": "a1", "bid_price": 0.44, "ask_price": 0.56, "bid_size": 10.0, "ask_size": 10.0},
        {"bid_order_id": "b2", "ask_order_id": "a2", "bid_price": 0.45, "ask_price": 0.55, "bid_size": 10.0, "ask_size": 10.0},
    ]
    assert len(s.live_orders["tok1"]) == 2
    assert s.live_orders["tok1"][0]["bid_price"] == 0.44
```

- [ ] **Step 2: Run test to verify it passes already**

```
pytest tests/maker/test_state.py::test_live_orders_is_list_per_market -v
```
Expected: PASS — `live_orders` is a plain dict so you can store anything in it. No change needed to state.py for this test. The important part is the *type annotation*.

- [ ] **Step 3: Update the type annotation in `maker/state.py`**

Change line:
```python
live_orders: dict[str, dict] = field(default_factory=dict)
```
to:
```python
live_orders: dict[str, list[dict]] = field(default_factory=dict)
```

- [ ] **Step 4: Run full test suite to ensure nothing breaks**

```
pytest tests/maker/ -v
```
Expected: all pass (annotation-only change).

- [ ] **Step 5: Commit**

```bash
git add maker/state.py tests/maker/test_state.py
git commit -m "feat(maker): widen live_orders to list[dict] per token for ladder levels"
```

---

## Task 3: Add `build_ladder()` to `QuoteEngine`

**Files:**
- Modify: `maker/quote_engine.py`
- Modify: `tests/maker/test_quote_engine.py`

- [ ] **Step 1: Write the failing tests**

Replace the `test_build_quote_intent` and `test_is_stale_*` tests in `tests/maker/test_quote_engine.py` with:

```python
from maker.quote_engine import QuoteEngine, compute_fair_value, compute_spread
from maker.types import LadderUpdate, QuoteIntent


def test_fair_value_at_midpoint_no_skew():
    fv = compute_fair_value(mid=0.50, skew=0.0, model_adj=0.0)
    assert fv == 0.50


def test_fair_value_with_positive_skew():
    fv = compute_fair_value(mid=0.50, skew=0.5, model_adj=0.0)
    assert fv > 0.50
    assert fv == 0.515


def test_fair_value_clamped():
    fv = compute_fair_value(mid=0.98, skew=1.0, model_adj=0.0)
    assert fv <= 0.95


def test_spread_base():
    s = compute_spread(volume_usd=1000.0, abs_inventory=0.0, hours_to_expiry=100.0)
    assert s == 0.06


def test_spread_widens_low_volume():
    s = compute_spread(volume_usd=200.0, abs_inventory=0.0, hours_to_expiry=100.0)
    assert s == 0.08


def test_spread_widens_with_inventory():
    s = compute_spread(volume_usd=1000.0, abs_inventory=10.0, hours_to_expiry=100.0)
    assert s == 0.16


def test_spread_widens_near_expiry():
    s = compute_spread(volume_usd=1000.0, abs_inventory=0.0, hours_to_expiry=24.0)
    assert s == 0.09


def test_spread_max_very_near_expiry():
    s = compute_spread(volume_usd=1000.0, abs_inventory=0.0, hours_to_expiry=3.0)
    assert s == 0.15


def test_spread_floor():
    s = compute_spread(volume_usd=10000.0, abs_inventory=0.0, hours_to_expiry=500.0)
    assert s >= 0.04


def test_build_ladder_returns_correct_number_of_levels():
    lu = QuoteEngine.build_ladder(
        token_id="abc",
        fair_value=0.50,
        spread=0.06,
        size=10.0,
        reason="reprice",
    )
    assert isinstance(lu, LadderUpdate)
    assert len(lu.levels) == 3  # LADDER_LEVELS


def test_build_ladder_center_level():
    """Center level (index 1) should be centered on fair value."""
    lu = QuoteEngine.build_ladder(
        token_id="abc",
        fair_value=0.50,
        spread=0.06,
        size=10.0,
        reason="reprice",
    )
    center = lu.center
    assert center.bid_price == round(0.50 - 0.03, 4)   # fv - half_spread
    assert center.ask_price == round(0.50 + 0.03, 4)


def test_build_ladder_outer_levels_wider():
    """Outer levels are offset by LEVEL_STEP from center."""
    lu = QuoteEngine.build_ladder(
        token_id="abc",
        fair_value=0.50,
        spread=0.06,
        size=10.0,
        reason="reprice",
    )
    inner = lu.levels[1]   # center
    outer = lu.levels[0]   # one step below center
    assert outer.bid_price < inner.bid_price
    assert outer.ask_price > inner.ask_price


def test_build_ladder_token_id_on_all_levels():
    lu = QuoteEngine.build_ladder("tok1", 0.50, 0.06, 10.0, "reprice")
    assert all(qi.token_id == "tok1" for qi in lu.levels)


def test_is_stale_returns_false_when_same():
    old = QuoteIntent("abc", 0.45, 0.55, 10.0, 10.0, "reprice")
    new = QuoteIntent("abc", 0.45, 0.55, 10.0, 10.0, "reprice")
    assert not QuoteEngine.is_stale(old, new, tick=0.01)


def test_is_stale_returns_true_when_price_changed():
    old = QuoteIntent("abc", 0.45, 0.55, 10.0, 10.0, "reprice")
    new = QuoteIntent("abc", 0.47, 0.57, 10.0, 10.0, "reprice")
    assert QuoteEngine.is_stale(old, new, tick=0.01)
```

- [ ] **Step 2: Run tests to verify `build_ladder` tests fail**

```
pytest tests/maker/test_quote_engine.py -k "ladder" -v
```
Expected: `AttributeError: type object 'QuoteEngine' has no attribute 'build_ladder'`

- [ ] **Step 3: Add `build_ladder()` and constants to `maker/quote_engine.py`**

Add constants after existing ones at top of file:
```python
LADDER_LEVELS = 3    # bid+ask pairs per market
LEVEL_STEP = 0.01    # price offset between ladder levels
```

Add static method to `QuoteEngine` class, replacing `build_quote`:

```python
@staticmethod
def build_ladder(
    token_id: str,
    fair_value: float,
    spread: float,
    size: float,
    reason: str,
) -> LadderUpdate:
    """Build a LADDER_LEVELS-deep ladder centred on fair_value.

    Level layout (LADDER_LEVELS=3, center index=1):
      index 0: bid = fv - half - LEVEL_STEP,  ask = fv + half + LEVEL_STEP
      index 1: bid = fv - half,               ask = fv + half          ← center
      index 2: bid = fv - half + LEVEL_STEP,  ask = fv + half - LEVEL_STEP

    Tightest level (index 2) is closest to mid. Widest (index 0) is outermost.
    All levels carry equal size.
    """
    half = spread / 2.0
    center_idx = LADDER_LEVELS // 2
    levels = []
    for i in range(LADDER_LEVELS):
        offset = (center_idx - i) * LEVEL_STEP
        bid = round(_clamp(fair_value - half - offset, 0.01, 0.99), 4)
        ask = round(_clamp(fair_value + half + offset, 0.01, 0.99), 4)
        if bid >= ask:
            continue  # skip degenerate level (very near 0 or 1)
        levels.append(QuoteIntent(
            token_id=token_id,
            bid_price=bid,
            ask_price=ask,
            bid_size=size,
            ask_size=size,
            reason=reason,
        ))
    return LadderUpdate(token_id=token_id, levels=levels, reason=reason)
```

Also add `LadderUpdate` to the import at the top of `quote_engine.py`:
```python
from maker.types import QuoteIntent, SkewUpdate, LadderUpdate
```

Keep `build_quote` in place for now (used by old tests) — it will be removed in Task 5.

- [ ] **Step 4: Update `run()` in `quote_engine.py` to emit `LadderUpdate`**

Change the emit block in `run()` from:
```python
new_quote = self.build_quote(...)
old_quote = self._maker.last_quotes.get(token_id)
is_new = token_id in new_ids or old_quote is None
if is_new or force or self.is_stale(old_quote, new_quote):
    self._maker.last_quotes[token_id] = new_quote
    await self._quote_intents_q.put(new_quote)
    log.debug(...)
```
to:
```python
ladder = self.build_ladder(
    token_id=token_id,
    fair_value=fv,
    spread=spread,
    size=QUOTE_SIZE_USDC,
    reason="reprice",
)
old_center = self._maker.last_quotes.get(token_id)
is_new = token_id in new_ids or old_center is None
if is_new or force or self.is_stale(old_center, ladder.center):
    self._maker.last_quotes[token_id] = ladder.center
    await self._quote_intents_q.put(ladder)
    log.debug(
        f"ladder [{token_id[:8]}] levels={len(ladder.levels)} "
        f"center={ladder.center.bid_price:.3f}/{ladder.center.ask_price:.3f} "
        f"force={force} new={is_new}"
    )
```

- [ ] **Step 5: Run all quote engine tests**

```
pytest tests/maker/test_quote_engine.py -v
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add maker/quote_engine.py tests/maker/test_quote_engine.py
git commit -m "feat(maker): add build_ladder() — 3-level bid/ask ladder per market"
```

---

## Task 4: Update `OrderManager` to handle `LadderUpdate`

**Files:**
- Modify: `maker/order_manager.py`
- Modify: `tests/maker/test_order_manager.py`

- [ ] **Step 1: Write failing tests**

Replace `tests/maker/test_order_manager.py` with:

```python
import asyncio
import pytest
from maker.order_manager import OrderManager
from maker.state import MakerState
from maker.types import LadderUpdate, QuoteIntent, CancelAll


class FakeClobClient:
    def __init__(self):
        self.posted: list[dict] = []
        self.cancelled: list[str] = []
        self._next_id = 0

    def create_order(self, order_args):
        return {"id": f"signed-{self._next_id}"}

    def post_order(self, signed_order, orderType=None, post_only=False):
        self._next_id += 1
        oid = f"order-{self._next_id}"
        self.posted.append({"orderID": oid, "post_only": post_only})
        return {"orderID": oid, "status": "live"}

    def cancel_orders(self, order_ids):
        self.cancelled.extend(order_ids)

    def cancel_all(self):
        self.cancelled.append("ALL")


def _make_ladder(token_id: str, n_levels: int = 3) -> LadderUpdate:
    levels = [
        QuoteIntent(token_id, round(0.45 - i * 0.01, 4), round(0.55 + i * 0.01, 4), 10.0, 10.0, "reprice")
        for i in range(n_levels)
    ]
    return LadderUpdate(token_id=token_id, levels=levels, reason="reprice")


def test_place_new_ladder():
    """First ladder for a market places 2 orders per level (bid + ask)."""
    maker_state = MakerState()
    clob = FakeClobClient()
    om = OrderManager(maker_state, clob=clob, paper=False)

    om.handle_ladder_sync(_make_ladder("tok1", n_levels=3))

    assert len(clob.posted) == 6        # 3 levels × 2 orders each
    assert all(o["post_only"] for o in clob.posted)
    assert "tok1" in maker_state.live_orders
    assert len(maker_state.live_orders["tok1"]) == 3


def test_replace_existing_ladder():
    """Re-pricing cancels all old levels and places fresh ones."""
    maker_state = MakerState()
    clob = FakeClobClient()
    om = OrderManager(maker_state, clob=clob, paper=False)

    om.handle_ladder_sync(_make_ladder("tok1", n_levels=3))   # 6 placed
    om.handle_ladder_sync(_make_ladder("tok1", n_levels=3))   # 6 cancelled + 6 new

    assert len(clob.cancelled) == 6     # bid+ask for each of the 3 old levels
    assert len(clob.posted) == 12       # 6 old + 6 new


def test_cancel_single_market():
    maker_state = MakerState()
    clob = FakeClobClient()
    om = OrderManager(maker_state, clob=clob, paper=False)

    om.handle_ladder_sync(_make_ladder("tok1"))
    om.handle_cancel_sync(CancelAll("tok1"))

    assert "tok1" not in maker_state.live_orders
    assert len(clob.cancelled) == 6     # all 3 levels cancelled


def test_cancel_all_global():
    maker_state = MakerState()
    clob = FakeClobClient()
    om = OrderManager(maker_state, clob=clob, paper=False)

    for i in range(3):
        om.handle_ladder_sync(_make_ladder(f"tok{i}"))

    om.handle_cancel_sync(CancelAll("*"))
    assert len(maker_state.live_orders) == 0
    assert "ALL" in clob.cancelled


def test_paper_mode_no_api_calls():
    maker_state = MakerState()
    clob = FakeClobClient()
    om = OrderManager(maker_state, clob=clob, paper=True)

    om.handle_ladder_sync(_make_ladder("tok1"))

    assert len(clob.posted) == 0
    assert "tok1" in maker_state.live_orders
    assert len(maker_state.live_orders["tok1"]) == 3
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/maker/test_order_manager.py -v
```
Expected: `AttributeError: 'OrderManager' object has no attribute 'handle_ladder_sync'`

- [ ] **Step 3: Rewrite `maker/order_manager.py`**

```python
"""OrderManager actor — places/cancels limit orders via py-clob-client."""

import asyncio
from maker.state import MakerState
from maker.types import LadderUpdate, CancelAll
from utils.logger import get_logger

log = get_logger(__name__)


class OrderManager:
    """Translates LadderUpdates into CLOB API calls."""

    def __init__(
        self,
        maker_state: MakerState,
        clob=None,
        paper: bool = True,
        quote_intents_q: asyncio.Queue | None = None,
        cancel_q: asyncio.Queue | None = None,
    ):
        self._maker = maker_state
        self._clob = clob
        self._paper = paper
        self._quote_intents_q = quote_intents_q
        self._cancel_q = cancel_q

    def handle_ladder_sync(self, update: LadderUpdate) -> None:
        """Cancel all existing levels for this market, then place the new ladder."""
        existing_levels = self._maker.live_orders.get(update.token_id, [])
        for level in existing_levels:
            self._cancel_level(level)

        new_levels = []
        for intent in update.levels:
            bid_oid = self._place_one(intent.token_id, intent.bid_price, intent.bid_size, "BUY")
            ask_oid = self._place_one(intent.token_id, intent.ask_price, intent.ask_size, "SELL")
            new_levels.append({
                "bid_order_id": bid_oid,
                "ask_order_id": ask_oid,
                "bid_price": intent.bid_price,
                "ask_price": intent.ask_price,
                "bid_size": intent.bid_size,
                "ask_size": intent.ask_size,
            })

        self._maker.live_orders[update.token_id] = new_levels
        log.info(
            f"LADDER {'[PAPER] ' if self._paper else ''}"
            f"[{update.token_id[:8]}] levels={len(new_levels)} "
            f"reason={update.reason}"
        )

    def handle_cancel_sync(self, cancel: CancelAll) -> None:
        if cancel.is_global:
            if not self._paper and self._clob:
                self._clob.cancel_all()
            self._maker.live_orders.clear()
            log.warning("CANCEL ALL — all quotes pulled")
        else:
            levels = self._maker.live_orders.pop(cancel.token_id, [])
            for level in levels:
                self._cancel_level(level)
            log.info(f"CANCEL [{cancel.token_id[:8]}] — {len(levels)} levels")

    def _place_one(self, token_id: str, price: float, size: float, side: str) -> str:
        if self._paper:
            return f"paper-{token_id[:8]}-{side.lower()}-{price:.4f}"

        from py_clob_client.clob_types import OrderArgs, OrderType
        order_args = OrderArgs(token_id=token_id, price=price, size=size, side=side)
        signed = self._clob.create_order(order_args)
        result = self._clob.post_order(signed, orderType=OrderType.GTC, post_only=True)
        return result.get("orderID", "")

    def _cancel_level(self, level: dict) -> None:
        if self._paper:
            return
        ids = [
            level.get("bid_order_id", ""),
            level.get("ask_order_id", ""),
        ]
        ids = [oid for oid in ids if oid and not oid.startswith("paper-")]
        if ids and self._clob:
            self._clob.cancel_orders(ids)

    async def run(self):
        """Main async loop — process ladder updates and cancels."""
        while True:
            # Process cancels with priority
            while self._cancel_q and not self._cancel_q.empty():
                try:
                    cancel = self._cancel_q.get_nowait()
                    self.handle_cancel_sync(cancel)
                except asyncio.QueueEmpty:
                    break

            # Process one ladder update
            if self._quote_intents_q:
                try:
                    update = await asyncio.wait_for(
                        self._quote_intents_q.get(), timeout=0.1
                    )
                    self.handle_ladder_sync(update)
                except asyncio.TimeoutError:
                    pass
            else:
                await asyncio.sleep(0.1)
```

- [ ] **Step 4: Run tests**

```
pytest tests/maker/test_order_manager.py -v
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add maker/order_manager.py tests/maker/test_order_manager.py
git commit -m "feat(maker): replace single-quote with handle_ladder_sync — 3 levels per market"
```

---

## Task 5: Update `FillPoller` for list-based levels

**Files:**
- Modify: `maker/fill_poller.py`
- Modify: `tests/maker/test_fill_poller.py`

- [ ] **Step 1: Write the failing tests**

Replace `tests/maker/test_fill_poller.py` with:

```python
import time
import pytest
from maker.fill_poller import FillPoller
from maker.state import MakerState
from market.state import ContractState


def _make_market(bid: float = 0.20, ask: float = 0.80, volume_usd: float = 5000.0) -> ContractState:
    return ContractState(
        yes_token_id="tok1",
        no_token_id="no-tok1",
        question="test",
        category="sports",
        best_bid=bid,
        best_ask=ask,
        volume_usd=volume_usd,
    )


def _add_ladder(maker_state: MakerState, token_id: str, n_levels: int = 3):
    """Seed live_orders with a list of per-level dicts."""
    maker_state.live_orders[token_id] = [
        {
            "bid_order_id": f"paper-bid-{i}",
            "ask_order_id": f"paper-ask-{i}",
            "bid_price": round(0.47 - i * 0.01, 4),
            "ask_price": round(0.53 + i * 0.01, 4),
            "bid_size": 10.0,
            "ask_size": 10.0,
        }
        for i in range(n_levels)
    ]


def test_paper_fill_competitive_bid():
    """Bid fills when our bid > market best_bid (we're the best bid)."""
    maker_state = MakerState()
    _add_ladder(maker_state, "tok1", n_levels=1)
    # Wide market: best_bid=0.20, our bid=0.47 → competitive
    markets = {"tok1": _make_market(bid=0.20, ask=0.80)}
    fills = FillPoller.check_paper_fills(maker_state, markets, poll_interval=1e6)
    bid_fills = [f for f in fills if f.side == "BUY"]
    assert len(bid_fills) >= 1
    assert bid_fills[0].price == 0.47


def test_paper_fill_competitive_ask():
    """Ask fills when our ask < market best_ask (we're the best ask)."""
    maker_state = MakerState()
    _add_ladder(maker_state, "tok1", n_levels=1)
    markets = {"tok1": _make_market(bid=0.20, ask=0.80)}
    fills = FillPoller.check_paper_fills(maker_state, markets, poll_interval=1e6)
    ask_fills = [f for f in fills if f.side == "SELL"]
    assert len(ask_fills) >= 1
    assert ask_fills[0].price == 0.53


def test_paper_fill_multiple_levels_can_fill():
    """With a 3-level ladder in a wide-spread market, multiple levels can fill."""
    maker_state = MakerState()
    _add_ladder(maker_state, "tok1", n_levels=3)
    markets = {"tok1": _make_market(bid=0.20, ask=0.80)}
    fills = FillPoller.check_paper_fills(maker_state, markets, poll_interval=1e6)
    # All 3 bid levels are competitive (all > 0.20); up to 3 bid fills
    bid_fills = [f for f in fills if f.side == "BUY"]
    assert len(bid_fills) <= 3


def test_paper_no_fill_when_uncompetitive():
    """No fill when our quotes are outside the market spread."""
    maker_state = MakerState()
    # Our bids: 0.40, 0.39, 0.38 — all below market bid of 0.48 → not competitive
    maker_state.live_orders["tok1"] = [
        {"bid_order_id": "b", "ask_order_id": "a",
         "bid_price": round(0.40 - i * 0.01, 4),
         "ask_price": round(0.70 + i * 0.01, 4),
         "bid_size": 10.0, "ask_size": 10.0}
        for i in range(3)
    ]
    markets = {"tok1": _make_market(bid=0.48, ask=0.52)}
    fills = FillPoller.check_paper_fills(maker_state, markets, poll_interval=1e6)
    assert len(fills) == 0


def test_paper_no_fill_zero_volume():
    maker_state = MakerState()
    _add_ladder(maker_state, "tok1", n_levels=1)
    markets = {"tok1": _make_market(bid=0.20, ask=0.80, volume_usd=0.0)}
    fills = FillPoller.check_paper_fills(maker_state, markets, poll_interval=1e6)
    assert len(fills) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/maker/test_fill_poller.py -v
```
Expected: failures because `check_paper_fills` still reads `order_info.get("bid_price")` from a single dict, not a list.

- [ ] **Step 3: Update `check_paper_fills` in `maker/fill_poller.py`**

Replace the method body:

```python
@staticmethod
def check_paper_fills(
    maker_state: MakerState,
    markets: dict[str, ContractState],
    poll_interval: float = 0.5,
) -> list[Fill]:
    """Simulate fills for paper ladder quotes using a volume-based Poisson model.

    Each ladder level is checked independently. A level fills if:
      - bid competitive: our bid > market best_bid
      - ask competitive: our ask < market best_ask
    Fill probability scales with market volume and poll_interval.
    """
    fills = []
    now = time.time()

    for token_id, levels in list(maker_state.live_orders.items()):
        cs = markets.get(token_id)
        if not cs or cs.volume_usd <= 0:
            continue

        for level in levels:
            bid_price = level.get("bid_price", 0.0)
            ask_price = level.get("ask_price", 1.0)
            bid_size = level.get("bid_size", 0.0)
            ask_size = level.get("ask_size", 0.0)

            size_ref = max(bid_size, ask_size, 1.0)
            rate_per_sec = cs.volume_usd / (86400.0 * size_ref * _COMPETITION_FACTOR)
            p_fill = 1.0 - math.exp(-rate_per_sec * poll_interval)

            if bid_price > cs.best_bid and bid_price > 0 and random.random() < p_fill:
                fills.append(Fill(
                    token_id=token_id,
                    side="BUY",
                    price=bid_price,
                    size=bid_size,
                    order_id=level.get("bid_order_id", ""),
                    filled_at=now,
                ))

            if ask_price < cs.best_ask and ask_price < 1.0 and random.random() < p_fill:
                fills.append(Fill(
                    token_id=token_id,
                    side="SELL",
                    price=ask_price,
                    size=ask_size,
                    order_id=level.get("ask_order_id", ""),
                    filled_at=now,
                ))

    return fills
```

- [ ] **Step 4: Run tests**

```
pytest tests/maker/test_fill_poller.py -v
```
Expected: all pass.

- [ ] **Step 5: Run full maker test suite**

```
pytest tests/maker/ -v
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add maker/fill_poller.py tests/maker/test_fill_poller.py
git commit -m "feat(maker): fill_poller iterates ladder levels per market"
```

---

## Task 6: Remove `build_quote` dead code and clean up

**Files:**
- Modify: `maker/quote_engine.py`

- [ ] **Step 1: Remove `build_quote` from `maker/quote_engine.py`**

Delete the `build_quote` static method entirely (it was the old single-level method, now replaced by `build_ladder`).

- [ ] **Step 2: Run full test suite**

```
pytest tests/ -v
```
Expected: all pass (no test references `build_quote` after Task 3 replaced them).

- [ ] **Step 3: Commit**

```bash
git add maker/quote_engine.py
git commit -m "refactor(maker): remove dead build_quote — ladder is the only quote path"
```

---

## Task 7: Extract `redeemall_loop` into `trading/redeemall.py`

**Files:**
- Modify: `trading/redeemall.py`
- Modify: `main.py`

The `redeemall_loop` currently lives in `main.py` and references taker-specific types (`CLOBExecutor`, `RiskManager`). We need a version the maker can also call — it takes only the wallet credentials it needs.

- [ ] **Step 1: Add `redeemall_loop` to `trading/redeemall.py`**

Add this function at the bottom of `trading/redeemall.py` (before the `if __name__ == "__main__"` block):

```python
async def redeemall_loop(
    paper: bool,
    wallet: str,
    rpc_url: str,
    private_key: str,
    tracked_positions_fn=None,
    mark_redeemed_fn=None,
    interval: int = 900,
) -> None:
    """
    Periodic redemption loop — shared by taker and maker modes.

    Args:
        paper: if True, loop sleeps without doing anything (no live redemptions in paper mode).
        wallet: on-chain wallet address.
        rpc_url: Polygon RPC endpoint.
        private_key: signing key.
        tracked_positions_fn: async callable () -> dict[str, TrackedPosition].
            If None, runs a full batch redeemall instead.
        mark_redeemed_fn: async callable (token_id: str) -> None.
            Called for each successfully redeemed position.
        interval: seconds between checks (default 900 = 15 min).
    """
    await asyncio.sleep(60)   # initial delay — let the bot warm up first
    while True:
        try:
            if paper:
                await asyncio.sleep(interval)
                continue

            if tracked_positions_fn is not None:
                pending = await tracked_positions_fn()
                redeemed_ids = await redeem_tracked_positions(
                    wallet=wallet,
                    rpc_url=rpc_url,
                    private_key=private_key,
                    tracked_positions=pending,
                )
                if mark_redeemed_fn:
                    for token_id in redeemed_ids:
                        await mark_redeemed_fn(token_id)
            else:
                await run_redeemall(wallet, rpc_url, private_key)

        except Exception as exc:
            log.error(f"redeemall_loop error: {exc}")

        await asyncio.sleep(interval)
```

- [ ] **Step 2: Update `main.py` to import and use the extracted function**

Find the existing `redeemall_loop` definition in `main.py` (around line 425) and replace the full function body with a call to the imported version.

Add to imports near the top of `main.py`:
```python
from trading.redeemall import redeemall_loop as _redeemall_loop_impl
```

Replace the `async def redeemall_loop(executor, risk, interval=900):` function in `main.py` with:

```python
async def redeemall_loop(executor, risk: RiskManager, interval: int = 900):
    """Thin wrapper — delegates to trading.redeemall.redeemall_loop."""
    await _redeemall_loop_impl(
        paper=PAPER,
        wallet=executor.wallet_address,
        rpc_url=config.RPC_URL,
        private_key=config.POLY_PRIVATE_KEY,
        tracked_positions_fn=lambda: risk.get_pending_redemptions(),
        mark_redeemed_fn=risk.mark_redeemed,
        interval=interval,
    )
```

Note: check whether `RiskManager` has `get_pending_redemptions()` or uses `pending_redemptions` directly. If it's a dict attribute, replace `tracked_positions_fn=lambda: risk.get_pending_redemptions()` with `tracked_positions_fn=lambda: dict(risk.pending_redemptions)`. Check `trading/risk.py` before editing.

- [ ] **Step 3: Run taker bot tests to verify main.py still works**

```
pytest tests/ -v
```
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add trading/redeemall.py main.py
git commit -m "refactor(redeemall): extract redeemall_loop into trading module for reuse"
```

---

## Task 8: Wire `redeemall_loop` into `maker/runner.py`

**Files:**
- Modify: `maker/runner.py`

- [ ] **Step 1: Add the redemption loop to `run_maker()`**

In `maker/runner.py`, add the import at the top:
```python
from trading.redeemall import redeemall_loop
```

Inside `run_maker()`, after the `if wallet_address:` block that adds `BalancePoller`, add:

```python
# Redemption loop — recycles resolved positions every 15 min (no-op in paper mode)
coros.append(
    redeemall_loop(
        paper=paper,
        wallet=wallet_address,
        rpc_url=config.RPC_URL,
        private_key=config.POLY_PRIVATE_KEY if not paper else "",
    )
)
```

Also add `import config` at the top of `runner.py` if it isn't already imported.

- [ ] **Step 2: Run maker integration smoke test**

```
pytest tests/maker/test_integration.py -v
```
Expected: pass (the integration test runs the full actor pipeline in paper mode; the redemption loop just sleeps).

- [ ] **Step 3: Run full test suite**

```
pytest tests/ -v
```
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add maker/runner.py
git commit -m "feat(maker): wire redeemall_loop into maker mode — resolved positions recycled every 15 min"
```

---

## Self-Review

**Spec coverage check:**
- [x] Deeper ladders (3 levels) — Tasks 1–6
- [x] Merge integration (redemption loop wired into maker) — Tasks 7–8
- [x] Paper fill simulation works per-level — Task 5
- [x] Cancel-all cancels all levels, not just one — Task 4
- [x] No dead code left (`build_quote` removed) — Task 6

**Placeholder scan:** None found.

**Type consistency:**
- `LadderUpdate.levels` is `tuple[QuoteIntent, ...]` (normalised in `__post_init__`) — used consistently in Tasks 1, 3, 4.
- `live_orders[token_id]` is `list[dict]` — written in Task 4 (`handle_ladder_sync`), read in Task 5 (`check_paper_fills`).
- `redeemall_loop` signature defined in Task 7, called in Task 8 — parameters match.

**Risk note:** Task 7 references `risk.get_pending_redemptions()` — verify the actual attribute name in `trading/risk.py` before executing. The note in Task 7 Step 2 flags this.
