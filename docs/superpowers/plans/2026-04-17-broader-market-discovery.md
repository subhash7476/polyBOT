# Broader Market Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three bugs in the market discovery pipeline so all 17 currently qualifying Polymarket markets get real-time WebSocket feeds and are quoted by the maker bot.

**Architecture:** Two independent fixes in two files. Task 1 corrects a volume-filter mismatch in `MarketSelector._discover_and_seed` (uses all-time volume instead of 24h volume). Task 2 raises the WebSocket subscription cap from 250 to 500 and adds a `max_size` parameter to prevent frame-size disconnects. No trading logic changes.

**Tech Stack:** Python asyncio, `websockets` library, `httpx`, `pytest` with `monkeypatch` and `AsyncMock`.

---

## File Map

| File | Change |
|------|--------|
| `maker/market_selector.py` | Line 377: fix volume filter in `_discover_and_seed` |
| `config.py` | Line 124: raise `MAX_SUBSCRIBED_MARKETS` default 250 → 500 |
| `market/clob_monitor.py` | Line 515: add `max_size=10*1024*1024` to `websockets.connect()` |
| `tests/maker/test_market_selector.py` | Add `test_discover_seed_uses_volume_24h_for_filter` |
| `tests/market/test_clob_selection.py` | Add `test_default_subscription_cap_is_500` |

---

## Task 1: Fix `_discover_and_seed` volume filter

**Files:**
- Modify: `maker/market_selector.py:377`
- Test: `tests/maker/test_market_selector.py`

### Background

`_discover_and_seed` is the secondary seeding path in `MarketSelector`. It runs every 15 minutes, scans Gamma API, and seeds markets into `app_state.markets` that CLOBMonitor may have missed. Its volume filter at line 377 reads:

```python
if meta.get("volume", 0) < _MIN_DAILY_VOLUME:
```

`meta["volume"]` is all-time CLOB volume. A new esports market like "FURIA win IEM Rio" may have $107k of 24h activity but near-zero all-time CLOB volume (most historical trading was through AMM, not the order book). This market silently fails the filter and never enters state.

`filter_and_rank` (line 239) already does this correctly:

```python
vol_check = cs.volume_24h if cs.volume_24h > 0 else cs.volume_usd
```

The fix aligns `_discover_and_seed` with `filter_and_rank`.

---

- [ ] **Step 1.1: Write the failing test**

Add at the bottom of `tests/maker/test_market_selector.py`:

```python
def test_discover_seed_uses_volume_24h_for_filter(monkeypatch):
    """_discover_and_seed must seed markets with high volume_24h even when
    all-time CLOB volume is zero.

    Bug: line 377 used meta.get("volume", 0) — all-time CLOB volume.
    A new esports market with volume=0 but volume_24h=$50k was silently
    excluded and never entered state.
    """
    import asyncio
    import importlib
    from datetime import datetime, timezone, timedelta
    from unittest.mock import AsyncMock, patch
    from market.state import AppState

    monkeypatch.delenv("MAKER_REQUIRE_END_DATE", raising=False)
    import maker.market_selector as ms_mod
    importlib.reload(ms_mod)

    expiry_soon = datetime.now(timezone.utc) + timedelta(days=2)
    new_market_meta = {
        "condition_id": "cnew",
        "question": "Will FURIA win IEM Rio 2026?",
        "category": "sports",
        "best_bid": 0.45,
        "best_ask": 0.52,       # 7c spread — passes spread filter
        "volume": 0.0,           # zero all-time CLOB volume → fails OLD filter
        "volume_24h": 50_000.0,  # $50k 24h volume → must pass NEW filter
        "no_token_id": "no-furia",
        "expiry": expiry_soon,
        "neg_risk": False,
        "fees_enabled": True,
    }

    app = AppState()
    selector = ms_mod.MarketSelector.__new__(ms_mod.MarketSelector)
    selector._state = app

    with patch(
        "maker.market_selector.fetch_active_markets",
        new=AsyncMock(return_value={"furia-yes": new_market_meta}),
    ):
        asyncio.run(selector._discover_and_seed())

    assert "furia-yes" in app.markets, (
        "Market with volume_24h=$50k but volume=0 must be seeded. "
        "Fix: use (meta.get('volume_24h', 0) or meta.get('volume', 0)) at line 377."
    )
```

- [ ] **Step 1.2: Run the test — verify it fails RED**

```bash
cd D:/bot/polymarket-bot
pytest tests/maker/test_market_selector.py::test_discover_seed_uses_volume_24h_for_filter -v
```

Expected output:
```
FAILED tests/maker/test_market_selector.py::test_discover_seed_uses_volume_24h_for_filter
AssertionError: Market with volume_24h=$50k but volume=0 must be seeded.
```

If it passes green, the bug is already fixed — re-check the line 377 condition before proceeding.

- [ ] **Step 1.3: Apply the fix**

In `maker/market_selector.py`, find line 377 (inside `_discover_and_seed`):

```python
# BEFORE — all-time CLOB volume only:
            if meta.get("volume", 0) < _MIN_DAILY_VOLUME:
```

Replace with:

```python
# AFTER — prefer 24h volume, fall back to all-time:
            if (meta.get("volume_24h", 0) or meta.get("volume", 0)) < _MIN_DAILY_VOLUME:
```

This is a one-line change. The pattern `(a or b)` returns `a` when `a` is truthy (non-zero), otherwise `b` — identical to how `filter_and_rank` handles it at line 239.

- [ ] **Step 1.4: Run the test — verify it passes GREEN**

```bash
pytest tests/maker/test_market_selector.py::test_discover_seed_uses_volume_24h_for_filter -v
```

Expected:
```
PASSED tests/maker/test_market_selector.py::test_discover_seed_uses_volume_24h_for_filter
```

- [ ] **Step 1.5: Run the full market selector test suite**

```bash
pytest tests/maker/test_market_selector.py -v
```

Expected: all tests pass. No regressions — the existing `test_discover_and_seed_force_seeds_falcon_spiking_markets` test uses markets with `volume=5_000_000` and `volume_24h=5_000_000`, so it is unaffected by this change.

- [ ] **Step 1.6: Commit**

```bash
git add maker/market_selector.py tests/maker/test_market_selector.py
git commit -m "fix(maker): _discover_and_seed filter by volume_24h not all-time volume

Markets with high 24h activity but zero all-time CLOB volume (new esports,
sports markets) were silently excluded. Align with filter_and_rank which
already uses volume_24h first.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 2: Raise WebSocket subscription cap and fix frame size

**Files:**
- Modify: `config.py:124`
- Modify: `market/clob_monitor.py:515`
- Test: `tests/market/test_clob_selection.py`

### Background

`CLOBMonitor` subscribes to markets for real-time price feeds via Polymarket's CLOB WebSocket. The cap is controlled by `MAX_SUBSCRIBED_MARKETS` (default 250 in `config.py`). Markets ranked 251+ by the hybrid sort key (`short_dated_bonus, -volume_24h, -liquidity, ...`) never get WS subscriptions and thus never receive real-time `best_bid`/`best_ask` updates.

Live testing confirmed the Polymarket WS server accepts 500–1000 asset subscriptions without error. The only failure mode is a client-side 1 MB receive frame limit in the `websockets` Python library: when 500 markets subscribe, Polymarket's initial order-book snapshots arrive in frames exceeding 1 MB, triggering `1009 (message too big)` disconnects. Adding `max_size=10*1024*1024` to the `websockets.connect()` call resolves this.

---

- [ ] **Step 2.1: Write the failing test**

Add at the bottom of `tests/market/test_clob_selection.py`:

```python
def test_default_subscription_cap_is_500():
    """The default MAX_SUBSCRIBED_MARKETS must be 500 to cover all qualifying maker markets.

    Before the fix: cm.MAX_SUBSCRIBED_MARKETS == 250 → this test FAILS.
    After the fix:  cm.MAX_SUBSCRIBED_MARKETS == 500 → this test PASSES.
    """
    import importlib
    import market.clob_monitor as cm_fresh
    # Reload to ensure we read the module default, not a monkeypatched value
    importlib.reload(cm_fresh)
    assert cm_fresh.MAX_SUBSCRIBED_MARKETS == 500, (
        f"Expected MAX_SUBSCRIBED_MARKETS=500, got {cm_fresh.MAX_SUBSCRIBED_MARKETS}. "
        "Raise the default in config.py line 124."
    )
```

- [ ] **Step 2.2: Run the test — verify it fails RED**

```bash
pytest tests/market/test_clob_selection.py::test_default_subscription_cap_is_500 -v
```

Expected:
```
FAILED tests/market/test_clob_selection.py::test_default_subscription_cap_is_500
AssertionError: Expected MAX_SUBSCRIBED_MARKETS=500, got 250.
```

- [ ] **Step 2.3: Raise the default cap in config.py**

In `config.py`, find line 124:

```python
# BEFORE:
MAX_SUBSCRIBED_MARKETS = int(os.getenv("MAX_SUBSCRIBED_MARKETS", "250"))
```

Change to:

```python
# AFTER:
MAX_SUBSCRIBED_MARKETS = int(os.getenv("MAX_SUBSCRIBED_MARKETS", "500"))
```

Operators running with an explicit `MAX_SUBSCRIBED_MARKETS=250` env-var are unaffected.

- [ ] **Step 2.4: Add max_size to websockets.connect in clob_monitor.py**

In `market/clob_monitor.py`, find line 515 inside `CLOBMonitor._run`:

```python
# BEFORE:
                    async with websockets.connect(POLYMARKET_WS_URL, ping_interval=20) as ws:
```

Change to:

```python
# AFTER:
                    async with websockets.connect(
                        POLYMARKET_WS_URL,
                        ping_interval=20,
                        max_size=10 * 1024 * 1024,   # 10 MB — handles 500-market book snapshots
                    ) as ws:
```

`10 * 1024 * 1024` = 10,485,760 bytes. At 500 subscriptions the largest observed snapshot frame was ~3–4 MB. This provides 2.5× headroom. The `websockets` default is 1 MB.

- [ ] **Step 2.5: Run the test — verify it passes GREEN**

```bash
pytest tests/market/test_clob_selection.py::test_default_subscription_cap_is_500 -v
```

Expected:
```
PASSED tests/market/test_clob_selection.py::test_default_subscription_cap_is_500
```

- [ ] **Step 2.6: Run the full clob selection test suite**

```bash
pytest tests/market/test_clob_selection.py -v
```

Expected: all tests pass. The existing tests all use `monkeypatch.setattr(cm, "MAX_SUBSCRIBED_MARKETS", N)` to set explicit values — they are unaffected by the default change.

- [ ] **Step 2.7: Run the full test suite**

```bash
pytest -x -q
```

Expected: all tests pass. If any test fails due to the `MAX_SUBSCRIBED_MARKETS` default change, it means that test was relying on the default value of 250 without monkeypatching — patch it explicitly using `monkeypatch.setattr(cm, "MAX_SUBSCRIBED_MARKETS", 250)`.

- [ ] **Step 2.8: Commit**

```bash
git add config.py market/clob_monitor.py tests/market/test_clob_selection.py
git commit -m "fix(clob): raise WS cap to 500 and allow 10MB frames

MAX_SUBSCRIBED_MARKETS 250→500: 17 qualifying maker markets are now
within the WS subscription budget and receive real-time price feeds.

max_size=10MB on websockets.connect: prevents 1009 frame-size disconnects
when Polymarket sends initial book snapshots for 500 markets simultaneously.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Verification After Both Tasks

- [ ] **Step 3.1: Restart the bot and watch the selector log**

After restarting, within the first 15-minute MarketSelector cycle, look for:

```
filter_and_rank: ... → N candidates → N selected
```

`N` should now be significantly higher than 3. All 17 qualifying markets (FURIA IEM Rio, tennis matches, Bitcoin $78k, temperature markets, etc.) should appear.

- [ ] **Step 3.2: Confirm WS subscription count in clob_monitor log**

```
subscribed to 500 markets on Polymarket CLOB WebSocket (re-discovery in 15m)
```

If it still shows 250, verify the `config.py` change was saved and the bot was fully restarted (not hot-reloaded).

- [ ] **Step 3.3: Confirm no 1009 frame errors in clob_monitor log**

```bash
grep "1009\|message too big\|frame" D:/bot/polymarket-bot/logs/clob_monitor.log
```

Expected: no output. If `1009` errors appear, the `max_size` change to `clob_monitor.py` was not saved correctly — re-check Step 2.4.
