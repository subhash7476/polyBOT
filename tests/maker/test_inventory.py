import asyncio
import time
import pytest
from maker.inventory import InventoryManager
from maker.state import MakerState
from maker.types import Fill, SkewUpdate, CancelAll


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
