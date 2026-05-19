# Maker Bot Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three bugs: frozen markout mid-prices, election markets bypassing the resolve-date filter, and high-volume Falcon spiking markets never reaching AppState.

**Architecture:** All changes are confined to two files: `market/clob_monitor.py` (Fix 1) and `maker/market_selector.py` (Fixes 2 & 3). No new files, no new dependencies. TDD throughout — write the failing test first, implement minimal fix, verify passing.

**Tech Stack:** Python 3.11+, asyncio, pytest, existing `market.state.AppState/ContractState`, `market.state.FeedState`, `maker.market_selector.MarketSelector`

---

## File Map

| File | Change |
|------|--------|
| `market/clob_monitor.py` | `_handle_trade()`: add directional bound update to `cs.best_bid`/`cs.best_ask` before putting in `trades_q` |
| `maker/market_selector.py` | Add `_REQUIRE_END_DATE` constant; add `n_no_date` counter + pre-check in `filter_and_rank()` and `_discover_and_seed()`; add Falcon force-seed in `_discover_and_seed()` |
| `tests/market/test_clob_monitor_trade.py` | New test file for Fix 1 |
| `tests/maker/test_market_selector.py` | Add tests for Fix 2 (no-date exclusion) and Fix 3 (Falcon force-seed) |

---

## Task 1: Fix `_handle_trade` mid-price update

**Context:** `_handle_trade()` in `market/clob_monitor.py` (lines 606-619) routes `last_trade_price` WebSocket events to `trades_q` for shadow fills. It never updates `cs.best_bid`/`cs.best_ask`. For thin weather markets that trade in bursts, no `price_change` events arrive in the T+5/30/60s markout windows, so mid stays frozen. The shadow fill poller reads `cs.mid` at fill time (stale), and the markout tracker reads it again at T+5/30/60 (still stale) → all markouts read 0.0.

**Fix pattern:** After parsing price, do a lockless directional bound update — same pattern used by `_handle_book()`. Update `cs.best_ask = price` if `price > cs.best_ask` (taker cleared the ask level), or `cs.best_bid = price` if `price < cs.best_bid` (taker hit the bid). Update happens **before** `trades_q.put_nowait()` so shadow fill poller reads the updated mid.

**Files:**
- Create: `tests/market/test_clob_monitor_trade.py`
- Modify: `market/clob_monitor.py:606-619`

- [ ] **Step 1: Write the failing test**

Create `tests/market/test_clob_monitor_trade.py`:

```python
"""Tests for _handle_trade mid-price update in CLOBMonitor."""
import asyncio
import pytest
from market.clob_monitor import CLOBMonitor
from market.state import AppState, ContractState


def _make_monitor_with_market(
    token_id: str,
    bid: float = 0.25,
    ask: float = 0.29,
) -> tuple[CLOBMonitor, AppState, asyncio.Queue]:
    """Build a minimal CLOBMonitor wired to a real AppState with one market."""
    app = AppState()
    cs = ContractState(
        yes_token_id=token_id,
        no_token_id="no-" + token_id,
        question="Will London be 13C?",
        category="weather",
        best_bid=bid,
        best_ask=ask,
        volume_usd=50_000.0,
    )
    app.markets[token_id] = cs
    trades_q: asyncio.Queue = asyncio.Queue()
    monitor = CLOBMonitor.__new__(CLOBMonitor)
    monitor._state = app
    monitor._trades_q = trades_q
    monitor._price_update_q = None
    monitor.log = __import__("utils.logger", fromlist=["get_logger"]).get_logger("test")
    return monitor, app, trades_q


def test_trade_above_ask_updates_best_ask():
    """Trade price above current best_ask → cs.best_ask raised to trade price."""
    monitor, app, trades_q = _make_monitor_with_market("tok1", bid=0.25, ask=0.29)
    msg = {"asset_id": "tok1", "price": "0.31", "size": "10"}
    monitor._handle_trade(msg)
    assert app.markets["tok1"].best_ask == pytest.approx(0.31)
    assert app.markets["tok1"].best_bid == pytest.approx(0.25)  # unchanged


def test_trade_below_bid_updates_best_bid():
    """Trade price below current best_bid → cs.best_bid lowered to trade price."""
    monitor, app, trades_q = _make_monitor_with_market("tok1", bid=0.25, ask=0.29)
    msg = {"asset_id": "tok1", "price": "0.20", "size": "5"}
    monitor._handle_trade(msg)
    assert app.markets["tok1"].best_bid == pytest.approx(0.20)
    assert app.markets["tok1"].best_ask == pytest.approx(0.29)  # unchanged


def test_trade_inside_spread_leaves_bid_ask_unchanged():
    """Trade inside spread should not update bid or ask."""
    monitor, app, trades_q = _make_monitor_with_market("tok1", bid=0.25, ask=0.29)
    msg = {"asset_id": "tok1", "price": "0.27", "size": "3"}
    monitor._handle_trade(msg)
    assert app.markets["tok1"].best_bid == pytest.approx(0.25)
    assert app.markets["tok1"].best_ask == pytest.approx(0.29)


def test_trade_still_enqueued_after_mid_update():
    """Even when bid/ask update, event must still reach trades_q."""
    monitor, app, trades_q = _make_monitor_with_market("tok1", bid=0.25, ask=0.29)
    msg = {"asset_id": "tok1", "price": "0.31", "size": "10"}
    monitor._handle_trade(msg)
    assert not trades_q.empty()
    token_id, price, size = trades_q.get_nowait()
    assert token_id == "tok1"
    assert price == pytest.approx(0.31)
    assert size == pytest.approx(10.0)


def test_trade_update_happens_before_enqueue():
    """cs.best_ask must be updated before the event lands in trades_q.
    (Shadow fill poller reads cs.mid after dequeuing — stale mid = wrong markout.)
    """
    updates = []

    class _TrackingQueue(asyncio.Queue):
        def put_nowait(self, item):
            # Capture mid at moment of enqueue
            cs = app.markets["tok1"]
            updates.append(cs.best_ask)
            super().put_nowait(item)

    app = AppState()
    cs = ContractState(
        yes_token_id="tok1", no_token_id="no-tok1",
        question="Q", category="weather",
        best_bid=0.25, best_ask=0.29, volume_usd=50_000.0,
    )
    app.markets["tok1"] = cs
    trades_q = _TrackingQueue()
    monitor = CLOBMonitor.__new__(CLOBMonitor)
    monitor._state = app
    monitor._trades_q = trades_q
    monitor._price_update_q = None
    monitor.log = __import__("utils.logger", fromlist=["get_logger"]).get_logger("test")

    monitor._handle_trade({"asset_id": "tok1", "price": "0.31", "size": "10"})
    # At enqueue time, best_ask must already be 0.31
    assert updates[0] == pytest.approx(0.31)


def test_unknown_token_no_crash():
    """Trade for a token not in AppState should silently drop (no KeyError)."""
    monitor, app, trades_q = _make_monitor_with_market("tok1")
    msg = {"asset_id": "unknown_tok", "price": "0.50", "size": "1"}
    monitor._handle_trade(msg)  # must not raise
    assert trades_q.empty()  # no queue entry for unknown token
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd D:/bot/polymarket-bot
pytest tests/market/test_clob_monitor_trade.py -v
```

Expected: All 6 tests **FAIL** — `test_trade_above_ask_updates_best_ask` fails because `best_ask` is still 0.29 not 0.31. `test_unknown_token_no_crash` may pass (current code already silently drops).

- [ ] **Step 3: Implement the fix in `market/clob_monitor.py`**

Replace lines 606-619 (the entire `_handle_trade` method):

```python
    def _handle_trade(self, msg: dict) -> None:
        """Route last_trade_price events to trades_q for shadow fill simulation.

        Also updates cs.best_ask / cs.best_bid as a directional bound when the
        trade price lies outside the current spread.  This keeps the markout
        tracker's mid reading accurate on thin markets where price_change events
        don't arrive between trades.

        Lockless update follows the _handle_book() pattern — acceptable because
        Python's GIL prevents torn writes on float attributes and a stale read
        is at worst one tick old.  The update happens BEFORE put_nowait so the
        shadow fill poller reads the corrected mid immediately on dequeue.
        """
        if self._trades_q is None:
            return
        token_id = msg.get("asset_id", "")
        price_raw = msg.get("price", None)
        if not token_id or price_raw is None:
            return
        try:
            price = float(price_raw)
            size = float(msg.get("size", 0.0))
            cs = self._state.markets.get(token_id)
            if cs is not None:
                if price > cs.best_ask:
                    cs.best_ask = price   # taker cleared ask — ask moved up at minimum
                elif price < cs.best_bid:
                    cs.best_bid = price   # taker hit bid — bid moved down at most
            self._trades_q.put_nowait((token_id, price, size))
        except (ValueError, asyncio.QueueFull):
            pass  # drop if queue full — ShadowFillPoller will catch next trade
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/market/test_clob_monitor_trade.py -v
```

Expected: All 6 tests **PASS**.

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
pytest tests/ -x -q
```

Expected: All existing tests pass.

- [ ] **Step 6: Commit**

```bash
git add market/clob_monitor.py tests/market/test_clob_monitor_trade.py
git commit -m "fix(clob_monitor): update best_bid/ask from trade price before enqueue

_handle_trade() now applies a directional bound update to ContractState
before putting the event in trades_q. Trade price above best_ask raises
best_ask; trade below best_bid lowers best_bid. Thin markets that trade
in bursts without emitting price_change events will now show correct mid
movement in markout T+5/30/60 samples instead of frozen 0.0 markouts."
```

---

## Task 2: Exclude markets with no resolve date

**Context:** `filter_and_rank()` in `maker/market_selector.py` (lines 249-260) has a guard `if cs.end_date_iso:` — when the field is empty, the entire date block is skipped. Election markets (Iowa/Wisconsin/Minnesota governors, Nov 2026) have no `endDateIso` from Gamma and `parse_contract()` can't infer one. They silently pass as if they had valid dates, creating 7-month inventory traps. `_discover_and_seed()` has a similar gap (lines 373-381) — it only checks date when `expiry is not None`, so markets with `expiry=None` also pass unchecked.

**Files:**
- Modify: `maker/market_selector.py:21-22` (add constant), `208` (add counter), `227-260` (add pre-check), `361` (add counter), `363-381` (add pre-check in seed)
- Test: `tests/maker/test_market_selector.py` (append new tests)

- [ ] **Step 1: Write failing tests** — append to `tests/maker/test_market_selector.py`

```python
# ── Fix 2: no-date exclusion ──────────────────────────────────────────────────

def test_no_end_date_excluded_by_default():
    """Market with empty end_date_iso must be excluded when MAKER_REQUIRE_END_DATE=true (default)."""
    markets = {
        "no_date": _make_market("no_date", bid=0.40, ask=0.60, volume=50_000.0),
    }
    # no_date market has end_date_iso="" by default (ContractState default)
    import os, importlib
    os.environ["MAKER_REQUIRE_END_DATE"] = "true"
    import maker.market_selector as ms_mod
    importlib.reload(ms_mod)
    selected = ms_mod.MarketSelector.filter_and_rank(markets)
    assert "no_date" not in selected


def test_no_end_date_allowed_when_require_disabled(monkeypatch):
    """Market with empty end_date_iso passes when MAKER_REQUIRE_END_DATE=false."""
    import os, importlib
    monkeypatch.setenv("MAKER_REQUIRE_END_DATE", "false")
    import maker.market_selector as ms_mod
    importlib.reload(ms_mod)
    markets = {
        "no_date": _make_market("no_date", bid=0.40, ask=0.60, volume=50_000.0),
    }
    selected = ms_mod.MarketSelector.filter_and_rank(markets)
    assert "no_date" in selected


def test_market_with_valid_end_date_still_passes():
    """Market with end_date_iso within the 7-day window must still be selected."""
    from datetime import datetime, timezone, timedelta
    future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    markets = {
        "has_date": _make_market("has_date", bid=0.40, ask=0.60, volume=50_000.0),
    }
    markets["has_date"].end_date_iso = future
    import os, importlib
    os.environ["MAKER_REQUIRE_END_DATE"] = "true"
    import maker.market_selector as ms_mod
    importlib.reload(ms_mod)
    selected = ms_mod.MarketSelector.filter_and_rank(markets)
    assert "has_date" in selected
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/maker/test_market_selector.py::test_no_end_date_excluded_by_default tests/maker/test_market_selector.py::test_no_end_date_allowed_when_require_disabled tests/maker/test_market_selector.py::test_market_with_valid_end_date_still_passes -v
```

Expected: First test **FAILS** (no_date market currently passes), others may pass or fail.

- [ ] **Step 3: Implement Fix 2 in `maker/market_selector.py`**

**3a.** Add constant after line 22 (after `_MIN_DAYS_TO_RESOLVE`):

```python
_REQUIRE_END_DATE = os.getenv("MAKER_REQUIRE_END_DATE", "true").lower() == "true"
```

**3b.** In `filter_and_rank()`, change line 208 (the diagnostic counters line):

```python
        n_excluded_cat = n_low_vol = n_bad_bid = n_far_future = n_tight_spread = n_too_soon = n_no_date = 0
```

**3c.** In `filter_and_rank()`, add a pre-check just before the existing `if cs.end_date_iso:` block (before line 249). The block to add goes between the `n_bad_bid` continue (line 244) and the comment on line 246:

```python
            # Resolve-date guard: exclude markets with no known resolution date.
            # Empty end_date_iso = Gamma didn't provide one and contract_parser
            # couldn't infer one (common for long-dated election markets).
            # Treating them as quotable creates 7-month inventory traps.
            if not cs.end_date_iso:
                if _REQUIRE_END_DATE:
                    n_no_date += 1
                    continue
```

**3d.** In `filter_and_rank()`, update the log line (around line 298-305) to include `no_date`:

```python
        log.info(
            f"filter_and_rank: {len(markets)} markets in state "
            f"(cats={cat_counts}) → "
            f"excluded_cat={n_excluded_cat} low_vol={n_low_vol} "
            f"tight_spread={n_tight_spread} bad_bid={n_bad_bid} "
            f"no_date={n_no_date} far_future={n_far_future} too_soon={n_too_soon} → "
            f"{len(candidates)} candidates → {min(len(candidates), max_markets)} selected"
        )
```

**3e.** In `_discover_and_seed()`, change the counter line (line 361):

```python
        n_excluded_cat = n_low_vol = n_bad_bid = n_far_future = n_no_date = 0
```

**3f.** In `_discover_and_seed()`, add a no-date guard just before the existing `expiry` check (before line 373). The block sits between the `n_bad_bid` continue and `expiry = meta.get("expiry")`:

```python
            # Skip markets with no resolve date when _REQUIRE_END_DATE is set.
            expiry_raw = meta.get("expiry")
            if expiry_raw is None:
                if _REQUIRE_END_DATE:
                    n_no_date += 1
                    continue
            expiry = expiry_raw
```

> **Note:** Remove the original `expiry = meta.get("expiry")` line on the old line 373 since we now assign it as `expiry = expiry_raw` above.

**3g.** Update the log line in `_discover_and_seed()` (around line 389-394):

```python
        log.info(
            f"_discover_and_seed: {len(token_map)} Gamma markets scanned "
            f"(excluded={n_excluded_cat} low_vol={n_low_vol} "
            f"bad_bid={n_bad_bid} no_date={n_no_date} far_future={n_far_future}) "
            f"→ {len(candidates)} candidates"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/maker/test_market_selector.py::test_no_end_date_excluded_by_default tests/maker/test_market_selector.py::test_no_end_date_allowed_when_require_disabled tests/maker/test_market_selector.py::test_market_with_valid_end_date_still_passes -v
```

Expected: All 3 **PASS**.

- [ ] **Step 5: Run full test suite**

```bash
pytest tests/ -x -q
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add maker/market_selector.py tests/maker/test_market_selector.py
git commit -m "fix(market_selector): exclude markets with no resolve date by default

Add _REQUIRE_END_DATE constant (default True). Markets with empty
end_date_iso now count as n_no_date and are excluded from both
filter_and_rank() and _discover_and_seed(). Prevents election markets
resolving in Nov 2026 (and other indefinite-duration markets) from
slipping through the far_future filter due to missing end date.
Set MAKER_REQUIRE_END_DATE=false env var to restore old permissive
behavior. Filter log now includes no_date=N count."
```

---

## Task 3: Force-seed Falcon spiking markets into AppState

**Context:** `_discover_and_seed()` ranks Gamma candidates by `spread × vol_rank`. The Iran market ($101M/d), Israel market ($44M/d), and Hungary PM market ($10M/d) all have tight spreads (~0.001–0.002), giving them near-zero scores. They fall below the `_MAX_ACTIVE_MARKETS * 2 = 60` seed window and never enter `AppState`. The DIAG loop can only report `NOT_IN_STATE`; there's no way to see whether they'd be `TIGHT` or otherwise. Fixing this means they enter state and `filter_and_rank()` correctly tags them as `TIGHT(spread=...)`.

**Note:** This does **not** change quoting behavior. These markets will still be excluded by `tight_spread` in `filter_and_rank()`. The change only improves diagnostic visibility.

**Files:**
- Modify: `maker/market_selector.py` — `_discover_and_seed()`, after the `top = candidates[:_MAX_ACTIVE_MARKETS * 2]` line
- Test: `tests/maker/test_market_selector.py` (append)

- [ ] **Step 1: Write failing test** — append to `tests/maker/test_market_selector.py`

```python
# ── Fix 3: Falcon force-seed ──────────────────────────────────────────────────

def test_discover_and_seed_force_seeds_falcon_spiking_markets():
    """Falcon spiking markets with tight spreads must be seeded into state
    even if their spread*vol score falls below the normal top-60 window."""
    import asyncio
    from unittest.mock import AsyncMock, patch, MagicMock
    from market.state import FalconMarketInsight

    # Build a token map: one wide-spread normal market (fills top slot),
    # one tight-spread spiking Falcon market (would normally be excluded).
    wide_meta = {
        "condition_id": "cwide",
        "question": "Will wide market resolve YES?",
        "category": "event",
        "best_bid": 0.40,
        "best_ask": 0.60,
        "volume": 5_000_000.0,
        "volume_24h": 5_000_000.0,
        "no_token_id": "no-wide",
        "expiry": None,
        "neg_risk": False,
        "fees_enabled": True,
    }
    tight_meta = {
        "condition_id": "ctight",
        "question": "US forces enter Iran by April 30?",
        "category": "event",
        "best_bid": 0.499,
        "best_ask": 0.501,  # 0.2¢ spread — far below _MIN_SPREAD
        "volume": 100_000_000.0,
        "volume_24h": 100_000_000.0,
        "no_token_id": "no-tight",
        "expiry": None,
        "neg_risk": False,
        "fees_enabled": True,
    }
    token_map = {"wide-yes": wide_meta, "tight-yes": tight_meta}

    # Build FalconMarketInsight for the tight market
    tight_insight = FalconMarketInsight(
        condition_id="ctight",
        question="US forces enter Iran by April 30?",
        volume_trend="Spiking",
        current_volume_24h=100_000_000.0,
        unique_traders_7d=14668,
        top1_wallet_pct=2.0,
        whale_control_flag=False,
        fetched_at=time.time(),
    )

    app = AppState()
    app.feeds.falcon_market_insights["ctight"] = tight_insight

    import os, importlib
    os.environ["MAKER_REQUIRE_END_DATE"] = "false"  # allow no-date markets in this test
    import maker.market_selector as ms_mod
    importlib.reload(ms_mod)

    selector = ms_mod.MarketSelector.__new__(ms_mod.MarketSelector)
    selector._state = app

    with patch("maker.market_selector.fetch_active_markets", new=AsyncMock(return_value=token_map)):
        asyncio.get_event_loop().run_until_complete(selector._discover_and_seed())

    # The tight Falcon spiking market must now be in state
    assert "tight-yes" in app.markets, "Falcon spiking market should be force-seeded"
    # The normal wide market should also be seeded
    assert "wide-yes" in app.markets
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest "tests/maker/test_market_selector.py::test_discover_and_seed_force_seeds_falcon_spiking_markets" -v
```

Expected: **FAIL** — `tight-yes` not in `app.markets`.

- [ ] **Step 3: Implement Fix 3 in `maker/market_selector.py`**

In `_discover_and_seed()`, after the line `top = candidates[:_MAX_ACTIVE_MARKETS * 2]` (after line 397), add the following Falcon force-seed block:

```python
        # Force-seed top Falcon spiking markets that fell below the spread×vol
        # ranking window.  These enter state so filter_and_rank() can report their
        # true exclusion reason (tight_spread) instead of NOT_IN_STATE.
        # Cap at top-20 spiking markets by 24h volume; skip whale-controlled ones.
        falcon_insights = self._state.feeds.falcon_market_insights
        if falcon_insights:
            # Build condition_id → yes_token_id reverse map from full Gamma scan
            cid_to_yes: dict[str, str] = {
                meta["condition_id"]: yes_id
                for yes_id, meta in token_map.items()
                if meta.get("condition_id")
            }
            top_candidate_cids = {meta["condition_id"] for _, meta, _ in top if meta.get("condition_id")}
            spiking = sorted(
                [
                    ins for ins in falcon_insights.values()
                    if ins.volume_trend == "Spiking"
                    and not ins.whale_control_flag
                    and ins.condition_id in cid_to_yes
                    and ins.condition_id not in top_candidate_cids
                    and (time.time() - ins.fetched_at) < _FALCON_STALE_SECONDS
                ],
                key=lambda x: -x.current_volume_24h,
            )[:20]
            for ins in spiking:
                yes_id = cid_to_yes[ins.condition_id]
                meta = token_map[yes_id]
                expiry = meta.get("expiry")
                end_date_iso = expiry.isoformat() if expiry and hasattr(expiry, "isoformat") else ""
                top.append((yes_id, meta, 0.0))  # score=0 — included for state visibility only
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest "tests/maker/test_market_selector.py::test_discover_and_seed_force_seeds_falcon_spiking_markets" -v
```

Expected: **PASS**.

- [ ] **Step 5: Run full test suite**

```bash
pytest tests/ -x -q
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add maker/market_selector.py tests/maker/test_market_selector.py
git commit -m "fix(market_selector): force-seed top Falcon spiking markets into state

High-volume spiking markets (Iran $101M/d, Israel $44M/d) have tight
spreads that score near-zero in the spread*vol ranking, putting them
below the 60-slot seed window. They never reached AppState so the DIAG
log could only show NOT_IN_STATE with no further detail.

Now, after the normal candidate ranking, up to 20 non-whale spiking
Falcon markets are force-appended to the seed list regardless of
spread score. They enter AppState and filter_and_rank() correctly
tags them as tight_spread= in the filter log."
```

---

## Self-Review

**Spec coverage:**
- Fix 1 (frozen markout mid): ✓ Task 1 covers `_handle_trade` update with ordering guarantee test
- Fix 2 (date filter bypass): ✓ Task 2 covers `filter_and_rank` + `_discover_and_seed`, `n_no_date` log, env var
- Fix 3 (Falcon force-seed): ✓ Task 3 covers `_discover_and_seed` extension with 20-market cap

**Placeholder scan:** No TBDs. All code blocks are complete. All `pytest` commands include exact paths.

**Type consistency:**
- `_handle_trade`: `cs = self._state.markets.get(token_id)` returns `ContractState | None` — `if cs is not None` guard is present ✓
- `_REQUIRE_END_DATE`: read at module load, used identically in both `filter_and_rank` and `_discover_and_seed` ✓
- `top.append((yes_id, meta, 0.0))` — same 3-tuple as existing `candidates.append` in `_discover_and_seed` ✓
- Task 2 Step 3f: replaces `expiry = meta.get("expiry")` with `expiry_raw = meta.get("expiry") / expiry = expiry_raw` — variable renamed consistently, `expiry` still used in the downstream `exp_ts` block ✓
