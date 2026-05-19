# Inventory Cap Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the inventory cap ratchet that causes ACTIVE MARKETS = 0 by raising thresholds, adding resume hysteresis, and implementing reduce-only quoting for orphaned positions.

**Architecture:** Three layered fixes: (1) raise `max_total_inventory` above current 653-share inventory to break the current deadlock and lower per-market cap to prevent future buildup; (2) add hysteresis to `cleanup_loop` so quotes don't resume until inventory drains to 80% of cap; (3) track orphaned positions (markets that left selection with open inventory) in `MarketSelector` and emit reduce-only ladders from `QuoteEngine` to actively drain them.

**Tech Stack:** Python asyncio, dataclasses (`maker/state.py`), `InventoryManager` (`maker/inventory.py`), `MarketSelector` (`maker/market_selector.py`), `QuoteEngine` (`maker/quote_engine.py`), `OrderManager` (`maker/order_manager.py`).

---

## File Map

| File | Change |
|------|--------|
| `maker/state.py` | Raise `max_total_inventory` 200→700, lower `max_inventory_per_market` 50→20, add `reduce_only_markets: set` field |
| `maker/inventory.py` | Add `TOTAL_INV_RESUME_RATIO = 0.80` constant; in `cleanup_loop` extend global cooldown if inventory still above resume threshold |
| `maker/market_selector.py` | In `run()`: track prev_selected, add deselected markets with open inventory to `reduce_only_markets`, clear on position close |
| `maker/quote_engine.py` | In `_reprice()`: for `reduce_only_markets`, zero out the expanding-side size before emitting `LadderUpdate` |
| `maker/order_manager.py` | In `_place_one()`: guard `size <= 0 → return ""` to skip placing zero-size orders |
| `tests/maker/test_inventory.py` | Add `test_default_cap_thresholds`, `test_cleanup_extends_cooldown_when_above_resume_threshold` |
| `tests/maker/test_market_selector.py` | Add `test_orphaned_market_added_to_reduce_only`, `test_closed_orphan_removed_from_reduce_only` |

---

### Task 1: Cap thresholds + `reduce_only_markets` field

**Files:**
- Modify: `maker/state.py:14-15`
- Test: `tests/maker/test_inventory.py`

- [ ] **Step 1: Write failing tests**

```python
# append to tests/maker/test_inventory.py

def test_default_cap_thresholds():
    s = MakerState()
    assert s.max_inventory_per_market == 20.0
    assert s.max_total_inventory == 700.0


def test_reduce_only_markets_field_exists():
    s = MakerState()
    assert hasattr(s, "reduce_only_markets")
    assert isinstance(s.reduce_only_markets, set)
    s.reduce_only_markets.add("tok1")
    assert "tok1" in s.reduce_only_markets
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/maker/test_inventory.py::test_default_cap_thresholds tests/maker/test_inventory.py::test_reduce_only_markets_field_exists -v
```

Expected: FAIL — `assert 20.0 == 50.0` and `AttributeError`.

- [ ] **Step 3: Change defaults and add field in `maker/state.py`**

Replace lines 14–15:
```python
    max_inventory_per_market: float = 20.0   # was 50 — tighter per-market limit
    max_total_inventory: float = 700.0       # was 200 — above current 653-share inventory
```

Add after the `inventory` field (after line 18 `inventory: dict[str, float] = field(default_factory=dict)`):
```python
    # Markets in reduce-only mode: position must shrink before new exposure allowed.
    # Populated by MarketSelector when a market exits selection with open inventory.
    reduce_only_markets: set = field(default_factory=set)
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/maker/test_inventory.py::test_default_cap_thresholds tests/maker/test_inventory.py::test_reduce_only_markets_field_exists -v
```

Expected: PASS.

- [ ] **Step 5: Verify existing inventory tests still pass**

```
pytest tests/maker/test_inventory.py -v
```

Expected: all existing tests pass (they use explicit fixture values `max_inventory_per_market=50.0, max_total_inventory=200.0` so they are unaffected).

- [ ] **Step 6: Commit**

```bash
git add maker/state.py tests/maker/test_inventory.py
git commit -m "feat(maker): raise total inv cap to 700, lower per-market to 20, add reduce_only_markets"
```

---

### Task 2: Hysteresis in `cleanup_loop`

**Files:**
- Modify: `maker/inventory.py:17` (add constant), `maker/inventory.py:243-262` (cleanup_loop body)
- Test: `tests/maker/test_inventory.py`

**Context:** `cleanup_loop` already runs every 300 s to expire resolved paper positions. We add a check: after expiry, if total inventory is still above 80% of cap (`TOTAL_INV_RESUME_RATIO = 0.80`), extend the global cooldown another 300 s. This prevents the ratchet where cooldown expires → quote resumes → fill → cap fires again.

- [ ] **Step 1: Write failing test**

```python
# append to tests/maker/test_inventory.py
import pytest

@pytest.mark.asyncio
async def test_cleanup_extends_cooldown_when_above_resume_threshold():
    """cleanup_loop extends global cooldown when inventory stays above 80% of cap."""
    from maker.inventory import InventoryManager, TOTAL_INV_RESUME_RATIO
    maker_state = MakerState(max_inventory_per_market=20.0, max_total_inventory=100.0)
    fills_q = asyncio.Queue()
    skew_q = asyncio.Queue()
    cancel_q = asyncio.Queue()
    im = InventoryManager(maker_state, fills_q, skew_q, cancel_q)

    # Load inventory above resume threshold (80% of 100 = 80 shares)
    for i in range(9):
        maker_state.update_inventory(f"tok{i}", "BUY", 9.0)  # total_abs = 81

    assert maker_state.total_abs_inventory == 81.0

    # Cooldown is not active now
    assert not maker_state.global_in_cooldown()

    # Call the internal expiry+hysteresis check
    await im._extend_cooldown_if_needed()

    # Cooldown should now be set
    assert maker_state.global_in_cooldown()
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/maker/test_inventory.py::test_cleanup_extends_cooldown_when_above_resume_threshold -v
```

Expected: FAIL — `AttributeError: 'InventoryManager' object has no attribute '_extend_cooldown_if_needed'` and `ImportError: cannot import name 'TOTAL_INV_RESUME_RATIO'`.

- [ ] **Step 3: Add constant and `_extend_cooldown_if_needed` to `maker/inventory.py`**

After line 20 (`MAX_DAILY_LOSS_PCT = 0.03`), add:
```python
# Hysteresis: after global cooldown expires, re-engage if inventory still above this
# fraction of max_total_inventory. Prevents the ratchet where expiry → fill → cap fires.
TOTAL_INV_RESUME_RATIO = 0.80
```

Add this method to `InventoryManager` (before `_expire_paper_positions`):
```python
    async def _extend_cooldown_if_needed(self) -> None:
        """Re-engage global cooldown if inventory is still above the resume threshold.

        Called from cleanup_loop after position expiry. Prevents the ratchet where
        the 300-s cooldown expires, quotes resume, a fill arrives, and the cap fires
        again — repeating indefinitely without draining inventory.
        """
        total = self._maker.total_abs_inventory
        resume_threshold = self._maker.max_total_inventory * TOTAL_INV_RESUME_RATIO
        if total > resume_threshold and not self._maker.global_in_cooldown():
            self._maker.global_cooldown_until = time.time() + COOLDOWN_SECONDS
            log.warning(
                f"INVENTORY RESUME BLOCKED: {total:.0f} > {resume_threshold:.0f} "
                f"(80% of {self._maker.max_total_inventory:.0f}) — "
                f"extending global cooldown {COOLDOWN_SECONDS}s"
            )
```

- [ ] **Step 4: Call `_extend_cooldown_if_needed` from `cleanup_loop`**

In `cleanup_loop` (around line 254), after the `if expired:` block, add the call:

```python
    async def cleanup_loop(self) -> None:
        """Periodic cleanup of stale paper positions."""
        await asyncio.sleep(60)
        while True:
            try:
                expired = await self._expire_paper_positions()
                if expired:
                    log.info(
                        f"Expired {expired} stale paper position(s); "
                        f"total_abs_inventory now {self._maker.total_abs_inventory:.0f} shares"
                    )
                await self._extend_cooldown_if_needed()
            except Exception as exc:
                log.exception(f"cleanup_loop error: {exc}")
            await asyncio.sleep(300)
```

- [ ] **Step 5: Run test to verify it passes**

```
pytest tests/maker/test_inventory.py::test_cleanup_extends_cooldown_when_above_resume_threshold -v
```

Expected: PASS.

- [ ] **Step 6: Run full inventory test suite**

```
pytest tests/maker/test_inventory.py -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add maker/inventory.py tests/maker/test_inventory.py
git commit -m "feat(maker): add inventory cap hysteresis — resume blocked until below 80% of cap"
```

---

### Task 3: Orphaned position cleanup

**Files:**
- Modify: `maker/market_selector.py:661-690` (`run()` method)
- Modify: `maker/quote_engine.py` (`_reprice()` method, around line 218)
- Modify: `maker/order_manager.py:69` (`_place_one()` method)
- Test: `tests/maker/test_market_selector.py`

**Context:** When a market leaves `MarketSelector`'s selection set, the bot stops quoting it but retains open inventory indefinitely. Adding these markets to `reduce_only_markets` lets `QuoteEngine` emit ladders where only the closing side has non-zero size, actively draining the position rather than waiting for natural resolution.

- [ ] **Step 1: Write failing tests for MarketSelector orphan detection**

```python
# append to tests/maker/test_market_selector.py

@pytest.mark.asyncio
async def test_orphaned_market_added_to_reduce_only():
    """When a market leaves selection and has non-zero inventory, it goes to reduce_only_markets."""
    from maker.market_selector import MarketSelector
    from maker.state import MakerState
    from market.state import AppState, FeedState

    maker_state = MakerState()
    maker_state.update_inventory("tok_old", "BUY", 10.0)

    app_state = AppState()
    app_state.feeds = FeedState()
    # Seed one market in state so MarketSelector can run
    app_state.markets["tok_new"] = _make_market("tok_new")

    active_q = asyncio.Queue()
    sel = MarketSelector(app_state, active_q, maker_state=maker_state)

    # Simulate previous selection containing tok_old
    maker_state.selected_token_ids = {"tok_old"}

    # Run one iteration — tok_old won't be in app_state.markets so it won't be selected
    await sel._run_one_cycle()

    assert "tok_old" in maker_state.reduce_only_markets


@pytest.mark.asyncio
async def test_closed_orphan_removed_from_reduce_only():
    """When orphaned position reaches zero inventory, it is removed from reduce_only_markets."""
    from maker.market_selector import MarketSelector
    from maker.state import MakerState
    from market.state import AppState, FeedState

    maker_state = MakerState()
    maker_state.reduce_only_markets.add("tok_closed")
    # inventory is already zero for tok_closed

    app_state = AppState()
    app_state.feeds = FeedState()
    app_state.markets["tok_new"] = _make_market("tok_new")

    active_q = asyncio.Queue()
    sel = MarketSelector(app_state, active_q, maker_state=maker_state)
    maker_state.selected_token_ids = set()

    await sel._run_one_cycle()

    assert "tok_closed" not in maker_state.reduce_only_markets
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/maker/test_market_selector.py::test_orphaned_market_added_to_reduce_only tests/maker/test_market_selector.py::test_closed_orphan_removed_from_reduce_only -v
```

Expected: FAIL — `AttributeError: 'MarketSelector' object has no attribute '_run_one_cycle'`.

- [ ] **Step 3: Extract `_run_one_cycle` from `run()` in `maker/market_selector.py`**

Replace the `run()` method body with a call to `_run_one_cycle()`:

```python
    async def _run_one_cycle(self) -> None:
        """One selection cycle: discover, rank, update state, emit to queue."""
        await self._discover_and_seed()

        async with self._state._lock:
            markets = dict(self._state.markets)
            feeds = self._state.feeds

        selected = self.filter_and_rank(markets, feeds=feeds)
        by_cat: dict[str, int] = {}
        for cs in selected.values():
            by_cat[cs.category] = by_cat.get(cs.category, 0) + 1

        log.info(
            f"MarketSelector: {len(selected)} markets selected "
            f"(from {len(markets)} total) — by_cat={by_cat}"
        )
        _falcon_overlap_report(selected, feeds)

        if self._maker_state is not None:
            async with self._maker_state._lock:
                prev_selected = set(self._maker_state.selected_token_ids)
                self._maker_state.selected_token_ids = set(selected.keys())

            # Orphaned position cleanup: markets that left selection with open inventory
            deselected = prev_selected - set(selected.keys())
            async with self._maker_state._lock:
                for token_id in deselected:
                    inv = self._maker_state.get_inventory(token_id)
                    if inv != 0.0:
                        self._maker_state.reduce_only_markets.add(token_id)
                        log.info(
                            f"Orphaned: [{token_id[:8]}] {inv:+.1f}sh — added to reduce_only"
                        )
                # Remove markets whose inventory has reached zero
                closed = {
                    t for t in self._maker_state.reduce_only_markets
                    if self._maker_state.get_inventory(t) == 0.0
                }
                self._maker_state.reduce_only_markets -= closed
                if closed:
                    log.info(f"Closed {len(closed)} orphaned position(s) — removed from reduce_only")

        await self._active_markets_q.put(set(selected.keys()))

    async def run(self):
        """Main loop — re-evaluate market selection every REFRESH_INTERVAL."""
        while True:
            async with self._state._lock:
                n = len(self._state.markets)
            if n > 0:
                break
            await asyncio.sleep(2.0)

        while True:
            await self._run_one_cycle()
            await asyncio.sleep(self.REFRESH_INTERVAL)
```

- [ ] **Step 4: Run market_selector tests to verify they pass**

```
pytest tests/maker/test_market_selector.py::test_orphaned_market_added_to_reduce_only tests/maker/test_market_selector.py::test_closed_orphan_removed_from_reduce_only -v
```

Expected: PASS.

- [ ] **Step 5: Guard zero-size orders in `maker/order_manager.py`**

In `_place_one`, add size guard at the top of the method:

```python
    def _place_one(self, token_id: str, price: float, size: float, side: str) -> str:
        if size <= 0:
            return ""   # skip zero-size side (reduce-only mode omits expanding side)
        if self._paper:
            return f"paper-{token_id[:8]}-{side.lower()}-{price:.4f}"
        # ... rest unchanged
```

- [ ] **Step 6: Add reduce-only ladder filtering to `maker/quote_engine.py`**

In `_reprice()`, right after `ladder = self.build_ladder(...)` (the line at ~line 218 that creates the `LadderUpdate`), add:

```python
            ladder = self.build_ladder(
                token_id=token_id,
                fair_value=eff_fv,
                spread=eff_spread,
                size=QUOTE_SIZE_USDC,
                reason="reprice",
            )

            # Reduce-only: orphaned markets may only quote on the closing side.
            # Net long → only asks (zero bid_size). Net short → only bids (zero ask_size).
            if token_id in self._maker.reduce_only_markets:
                inv = self._maker.get_inventory(token_id)
                if inv != 0.0:
                    new_levels = tuple(
                        QuoteIntent(
                            l.token_id, l.bid_price, l.ask_price,
                            0.0 if inv > 0 else l.bid_size,
                            0.0 if inv < 0 else l.ask_size,
                            "reduce_only",
                        )
                        for l in ladder.levels
                    )
                    ladder = LadderUpdate(token_id, new_levels, "reduce_only")
```

`QuoteIntent` is already imported at the top of quote_engine.py via `from maker.types import QuoteIntent, SkewUpdate, LadderUpdate, CancelAll`.

- [ ] **Step 7: Run full test suite**

```
pytest tests/maker/ -v
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add maker/market_selector.py maker/quote_engine.py maker/order_manager.py tests/maker/test_market_selector.py
git commit -m "feat(maker): orphaned position cleanup — reduce-only quoting for deselected markets with open inventory"
```

---

## Self-Review

**Spec coverage:**
1. ✅ Raise `max_total_inventory` to 700 — Task 1
2. ✅ Lower `max_inventory_per_market` to 20 — Task 1
3. ✅ Cap hysteresis (resume only below 80% threshold) — Task 2
4. ✅ Orphaned position cleanup (detect + reduce-only quote) — Task 3

**Placeholder scan:** No TBDs, no "add appropriate handling", all code blocks complete.

**Type consistency:**
- `reduce_only_markets` is `set` throughout (state.py field, market_selector reads/writes it, quote_engine reads it)
- `_extend_cooldown_if_needed` is defined before use in `cleanup_loop`
- `_run_one_cycle` is defined before `run()` calls it
- `QuoteIntent` constructor arguments match the frozen dataclass: `token_id, bid_price, ask_price, bid_size, ask_size, reason`
