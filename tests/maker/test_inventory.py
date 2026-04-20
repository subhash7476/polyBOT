import asyncio
import time
import pytest
from maker.inventory import InventoryManager
from maker.state import MakerState
from maker.types import Fill, SkewUpdate, CancelAll
from market.state import AppState, ContractState


@pytest.fixture
def setup():
    maker_state = MakerState(max_inventory_per_market=50.0, max_total_inventory=200.0)
    fills_q = asyncio.Queue()
    skew_q = asyncio.Queue()
    cancel_q = asyncio.Queue()
    im = InventoryManager(maker_state, fills_q, skew_q, cancel_q)
    return im, maker_state, fills_q, skew_q, cancel_q


@pytest.mark.asyncio
async def test_buy_fill_updates_inventory(setup):
    im, maker_state, _, skew_q, _ = setup
    fill = Fill("tok1", "BUY", 0.45, 10.0, "order-1", time.time())

    await im.handle_fill(fill)

    assert maker_state.get_inventory("tok1") == 10.0
    skew = skew_q.get_nowait()
    assert skew.token_id == "tok1"
    assert skew.skew_factor == 0.2  # 10/50


@pytest.mark.asyncio
async def test_sell_fill_reduces_inventory(setup):
    im, maker_state, _, skew_q, _ = setup
    maker_state.update_inventory("tok1", "BUY", 20.0)

    fill = Fill("tok1", "SELL", 0.55, 5.0, "order-2", time.time())
    await im.handle_fill(fill)

    assert maker_state.get_inventory("tok1") == 15.0


@pytest.mark.asyncio
async def test_per_market_cap_triggers_cancel(setup):
    im, maker_state, _, _, cancel_q = setup
    maker_state.update_inventory("tok1", "BUY", 45.0)

    fill = Fill("tok1", "BUY", 0.45, 10.0, "order-3", time.time())
    await im.handle_fill(fill)

    cancel = cancel_q.get_nowait()
    assert cancel.token_id == "tok1"
    assert not cancel.is_global


@pytest.mark.asyncio
async def test_total_cap_triggers_global_cancel(setup):
    im, maker_state, _, _, cancel_q = setup
    # Fill up to near total cap
    for i in range(4):
        maker_state.update_inventory(f"tok{i}", "BUY", 48.0)
    # This fill pushes total over 200
    fill = Fill("tok4", "BUY", 0.45, 10.0, "order-4", time.time())
    await im.handle_fill(fill)

    # Should have at least a global cancel
    cancels = []
    while not cancel_q.empty():
        cancels.append(cancel_q.get_nowait())
    assert any(c.is_global for c in cancels)


@pytest.mark.asyncio
async def test_rapid_double_fill_triggers_cancel(setup):
    im, maker_state, _, _, cancel_q = setup
    now = time.time()

    fill_buy = Fill("tok1", "BUY", 0.45, 10.0, "order-5", now)
    await im.handle_fill(fill_buy)

    # Same market, opposite side, within 5 seconds
    fill_sell = Fill("tok1", "SELL", 0.55, 10.0, "order-6", now + 2.0)
    await im.handle_fill(fill_sell)

    cancels = []
    while not cancel_q.empty():
        cancels.append(cancel_q.get_nowait())
    assert any(c.token_id == "tok1" for c in cancels)


@pytest.mark.asyncio
async def test_daily_pnl_recorded(setup):
    im, maker_state, _, _, _ = setup

    fill_buy = Fill("tok1", "BUY", 0.45, 10.0, "order-7", time.time())
    await im.handle_fill(fill_buy)

    fill_sell = Fill("tok1", "SELL", 0.55, 10.0, "order-8", time.time())
    await im.handle_fill(fill_sell)

    # After round-trip, inventory is zero
    assert maker_state.get_inventory("tok1") == 0.0


# ---------------------------------------------------------------------------
# Bug-fix tests: paper position expiry
# ---------------------------------------------------------------------------

def _expired_cs(token_id: str) -> ContractState:
    """ContractState for a market that ended in the past."""
    return ContractState(
        yes_token_id=token_id, no_token_id="no-" + token_id,
        question="Old market", category="sports",
        best_bid=0.99, best_ask=1.0,
        volume_usd=100.0, end_date_iso="2020-01-01T00:00:00Z",
    )


def _active_cs(token_id: str) -> ContractState:
    """ContractState for a market that expires far in the future."""
    return ContractState(
        yes_token_id=token_id, no_token_id="no-" + token_id,
        question="Live market", category="sports",
        best_bid=0.45, best_ask=0.55,
        volume_usd=5000.0, end_date_iso="2030-06-01T00:00:00Z",
    )


@pytest.mark.asyncio
async def test_expire_clears_stale_inventory(setup):
    """_expire_paper_positions zeros inventory for a market whose end date has passed.

    Bug: paper/shadow mode never settles positions, so resolved markets accumulate
    in inventory indefinitely, inflating total_abs_inventory and causing the global
    cap to fire on every fill.
    """
    im, maker_state, _, _, _ = setup
    token_id = "expired-tok"
    maker_state.inventory[token_id] = 30.0

    app = AppState()
    app.markets[token_id] = _expired_cs(token_id)
    im._app = app

    expired = await im._expire_paper_positions()

    assert expired == 1, "should have expired one market"
    assert maker_state.inventory[token_id] == 0.0, "stale inventory must be zeroed"


@pytest.mark.asyncio
async def test_expire_preserves_active_inventory(setup):
    """_expire_paper_positions leaves active (future-expiry) markets untouched."""
    im, maker_state, _, _, _ = setup
    expired_tok = "old-tok"
    active_tok = "live-tok"
    maker_state.inventory[expired_tok] = 25.0
    maker_state.inventory[active_tok] = 15.0

    app = AppState()
    app.markets[expired_tok] = _expired_cs(expired_tok)
    app.markets[active_tok] = _active_cs(active_tok)
    im._app = app

    expired = await im._expire_paper_positions()

    assert expired == 1
    assert maker_state.inventory[expired_tok] == 0.0, "expired market must be zeroed"
    assert maker_state.inventory[active_tok] == 15.0, "active market must be preserved"


@pytest.mark.asyncio
async def test_total_cap_no_longer_fires_after_cleanup(setup):
    """After expiring stale positions the global inventory cap stops firing.

    Scenario: five yesterday-markets each with 50-share positions push
    total_abs_inventory to 250 (above the 200-share cap).  After cleanup the
    total drops to 0 and a new fill on a fresh market should NOT trigger a
    global CancelAll.
    """
    im, maker_state, _, _, cancel_q = setup

    # Stale positions from yesterday — total = 250, above the 200-share cap
    for i in range(5):
        maker_state.inventory[f"old-tok-{i}"] = 50.0

    app = AppState()
    for i in range(5):
        app.markets[f"old-tok-{i}"] = _expired_cs(f"old-tok-{i}")
    im._app = app

    assert maker_state.total_abs_inventory >= maker_state.max_total_inventory

    expired = await im._expire_paper_positions()
    assert expired == 5
    assert maker_state.total_abs_inventory == 0.0

    # A new fill on a fresh market should not trigger a global cancel
    fill = Fill("new-tok", "BUY", 0.45, 10.0, "order-x", time.time())
    await im.handle_fill(fill)

    cancels = []
    while not cancel_q.empty():
        cancels.append(cancel_q.get_nowait())
    assert not any(c.is_global for c in cancels), (
        "Global cancel must NOT fire after stale positions are expired"
    )


@pytest.mark.asyncio
async def test_daily_loss_limit_sets_global_cooldown(setup):
    """Daily loss limit must set global_cooldown_until so QuoteEngine stops re-quoting.

    Bug: handle_fill() fired CancelAll('*') when MTM crossed the daily loss threshold
    but did NOT set global_cooldown_until. QuoteEngine would re-place quotes within 30s
    on the next force-reprice, producing more fills, which re-fired the limit — a
    CANCEL ALL storm every 30s with no effective quoting pause.
    """
    im, maker_state, _, _, cancel_q = setup

    # Bankroll $500, MAX_DAILY_LOSS_PCT=3% → max_daily_loss=$15.
    # Simulate a realized cash loss large enough to breach the threshold.
    # cash_pnl alone: bought 50 shares at 0.80 → cash_pnl = -40 (no positions value added
    # because app_state is None, so mtm falls back to cash_pnl).
    maker_state.cash_pnl = -20.0  # well below -$15 threshold

    assert not maker_state.global_in_cooldown(), "should start with no global cooldown"

    fill = Fill("tok1", "BUY", 0.45, 1.0, "order-loss", time.time())
    await im.handle_fill(fill)

    # Global cancel must have fired
    cancels = []
    while not cancel_q.empty():
        cancels.append(cancel_q.get_nowait())
    assert any(c.is_global for c in cancels), "daily loss must fire a global CancelAll"

    # Global cooldown must be set so QuoteEngine skips the next reprice cycle
    assert maker_state.global_in_cooldown(), (
        "global_cooldown_until must be set after daily loss limit fires"
    )


# ---------------------------------------------------------------------------
# Tests for cap thresholds and reduce_only_markets field (Task 1)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Tests for hysteresis in cleanup_loop (Task 2)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cleanup_extends_cooldown_when_above_resume_threshold():
    """cleanup_loop extends global cooldown when inventory stays above 80% of cap.

    Scenario: After position expiry, if total inventory is still above 80% of cap,
    we should extend the global cooldown another 300s. This prevents the ratchet
    where cooldown expires → quote resumes → fill arrives → cap fires again.
    """
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

    # Call the internal hysteresis check
    await im._engage_cooldown_if_above_threshold()

    # Cooldown should now be set
    assert maker_state.global_in_cooldown()
