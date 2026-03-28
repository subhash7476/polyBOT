import asyncio
import pytest
from market.state import FeedState
from trading.risk import RiskManager
from engine.contract_parser import ParsedContract
from datetime import datetime, timezone, timedelta


def make_parsed(asset="BTC", direction="above") -> ParsedContract:
    return ParsedContract(
        token_id="t1", question="q",
        asset=asset, direction=direction,
        target_price=90000.0,
        expiry=datetime.now(timezone.utc) + timedelta(days=30),
    )


@pytest.fixture
def rm(tmp_path):
    from trading.positions import PositionLedger
    ledger = PositionLedger(str(tmp_path / "test_positions.jsonl"))
    return RiskManager(bankroll=1000.0, ledger=ledger)


@pytest.fixture
def feeds():
    fs = FeedState()
    fs.dvol["BTC"] = 60.0
    return fs


@pytest.mark.asyncio
async def test_can_trade_initially(rm, feeds):
    ok, msg = await rm.can_trade("t1", make_parsed(), 50.0, feeds)
    assert ok is True


@pytest.mark.asyncio
async def test_rejects_oversized_position(rm, feeds):
    ok, msg = await rm.can_trade("t1", make_parsed(), 200.0, feeds)  # >10% of 1000
    assert ok is False
    assert "max" in msg


@pytest.mark.asyncio
async def test_rejects_after_daily_loss(rm, feeds):
    rm.daily_pnl = -60.0  # -6% of 1000
    ok, msg = await rm.can_trade("t1", make_parsed(), 10.0, feeds)
    assert ok is False
    assert "daily loss" in msg


@pytest.mark.asyncio
async def test_rejects_at_max_open_positions(rm, feeds):
    for i in range(5):
        await rm.open_position(f"t{i}", make_parsed(), 20.0, 0.5)
    ok, msg = await rm.can_trade("t_new", make_parsed(), 20.0, feeds)
    assert ok is False
    assert "max" in msg


@pytest.mark.asyncio
async def test_group_exposure_limit(rm, feeds):
    # Fill BTC-above group to 25% of bankroll (250)
    await rm.open_position("t1", make_parsed("BTC", "above"), 250.0, 0.5)
    ok, msg = await rm.can_trade("t2", make_parsed("BTC", "above"), 10.0, feeds)
    assert ok is False
    assert "group" in msg


@pytest.mark.asyncio
async def test_different_group_allowed(rm, feeds):
    # BTC above is full, but BTC below group is still open
    await rm.open_position("t1", make_parsed("BTC", "above"), 250.0, 0.5)
    ok, _ = await rm.can_trade("t2", make_parsed("BTC", "below"), 20.0, feeds)
    assert ok is True


@pytest.mark.asyncio
async def test_high_vol_throttles_large_position(rm, feeds):
    feeds.dvol["BTC"] = 85.0
    ok, msg = await rm.can_trade("t1", make_parsed(), 80.0, feeds)  # >50% of max_pos=100
    assert ok is False
    assert "vol" in msg.lower() or "dvol" in msg.lower()


@pytest.mark.asyncio
async def test_close_position_records_pnl(rm, feeds):
    await rm.open_position("t1", make_parsed(), 50.0, 0.5)
    await rm.close_position("t1", exit_price=0.8)
    # $50 at 0.50 buys 100 shares; 30c move => $30 pnl
    assert rm.daily_pnl == pytest.approx(30.0)


@pytest.mark.asyncio
async def test_consecutive_losses_increase_ev_multiplier(rm, feeds):
    assert rm.ev_multiplier == 1.0
    rm.consecutive_losses = 3
    assert rm.ev_multiplier == 2.0


@pytest.mark.asyncio
async def test_winning_trade_resets_consecutive_losses(rm, feeds):
    await rm.open_position("t1", make_parsed(), 50.0, 0.5)
    rm.consecutive_losses = 3
    await rm.close_position("t1", exit_price=0.8)  # win
    assert rm.consecutive_losses == 0


@pytest.mark.asyncio
async def test_open_position_stores_side(rm, feeds):
    await rm.open_position("t1", make_parsed(), 50.0, 0.5, side="BUY_NO")
    assert rm.open_positions["t1"].side == "BUY_NO"
