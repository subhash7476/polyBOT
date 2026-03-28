"""Tests for dashboard/loops.py — asyncio coroutines that snapshot AppState into DashboardState."""
import asyncio
import json
import time

import pytest

from market.state import AppState, FeedState, ContractState
from dashboard.state import DashboardState
from dashboard.loops import dashboard_loop, update_scan_stats


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app_state_with_feeds() -> AppState:
    state = AppState()
    state.feeds.spot_prices["BTC"] = 85000.0
    state.feeds.spot_prices["ETH"] = 3200.0
    state.feeds.dvol["BTC"] = 72.0
    state.feeds.funding_rates["BTC"] = 0.0001
    state.feeds.dxy = 104.5
    state.feeds.fed_may_cut_prob = 0.35
    state.feeds.sofr = 3.65
    return state


# ---------------------------------------------------------------------------
# update_scan_stats (synchronous helper — no asyncio needed)
# ---------------------------------------------------------------------------

def test_update_scan_stats_writes_all_keys():
    ds = DashboardState()
    update_scan_stats(ds, n_total=24, n_parseable=20, n_signal=4,
                      n_liquidity=4, n_ev=2, n_traded=1)
    assert ds.scan_stats["n_total"] == 24
    assert ds.scan_stats["n_parseable"] == 20
    assert ds.scan_stats["n_signal"] == 4
    assert ds.scan_stats["n_liquidity"] == 4
    assert ds.scan_stats["n_ev"] == 2
    assert ds.scan_stats["n_traded"] == 1
    assert ds.scan_stats["lifetime_trades"] == 1


def test_update_scan_stats_partial_kwargs():
    ds = DashboardState()
    update_scan_stats(ds, n_total=10, n_traded=0)
    assert ds.scan_stats["n_total"] == 10
    assert ds.scan_stats["n_traded"] == 0
    assert ds.scan_stats["lifetime_trades"] == 0


def test_update_scan_stats_n_traded_is_cumulative():
    ds = DashboardState()
    update_scan_stats(ds, n_total=10, n_traded=1)
    update_scan_stats(ds, n_total=30, n_traded=2)
    assert ds.scan_stats["n_total"] == 30
    assert ds.scan_stats["n_traded"] == 2
    assert ds.scan_stats["lifetime_trades"] == 3


# ---------------------------------------------------------------------------
# dashboard_loop (asyncio coroutine)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dashboard_loop_copies_spot_prices():
    state = _make_app_state_with_feeds()
    ds = DashboardState()
    # Run one tick then cancel
    task = asyncio.create_task(dashboard_loop(state, ds, interval=0.05))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert ds.spot_prices.get("BTC") == 85000.0
    assert ds.spot_prices.get("ETH") == 3200.0


@pytest.mark.asyncio
async def test_dashboard_loop_copies_dvol():
    state = _make_app_state_with_feeds()
    ds = DashboardState()
    task = asyncio.create_task(dashboard_loop(state, ds, interval=0.05))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert ds.dvol.get("BTC") == 72.0


@pytest.mark.asyncio
async def test_dashboard_loop_copies_macro_fields():
    state = _make_app_state_with_feeds()
    ds = DashboardState()
    task = asyncio.create_task(dashboard_loop(state, ds, interval=0.05))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert ds.dxy == 104.5
    assert abs(ds.fed_may_cut_prob - 0.35) < 1e-9
    assert ds.sofr == 3.65


@pytest.mark.asyncio
async def test_dashboard_loop_updates_uptime():
    state = _make_app_state_with_feeds()
    ds = DashboardState()
    task = asyncio.create_task(dashboard_loop(state, ds, interval=0.05))
    await asyncio.sleep(0.15)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert ds.uptime_seconds >= 0


@pytest.mark.asyncio
async def test_dashboard_loop_sets_binance_feed_timestamp():
    state = _make_app_state_with_feeds()
    ds = DashboardState()
    before = time.time()
    task = asyncio.create_task(dashboard_loop(state, ds, interval=0.05))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # If spot prices are present, binance timestamp should be set
    assert "binance" in ds.feed_updated_at
    assert ds.feed_updated_at["binance"] >= before


@pytest.mark.asyncio
async def test_dashboard_loop_runs_repeatedly():
    state = _make_app_state_with_feeds()
    ds = DashboardState()
    tick_count = 0
    original_update = ds.update

    def counting_update(snapshot):
        nonlocal tick_count
        tick_count += 1
        original_update(snapshot)

    ds.update = counting_update

    task = asyncio.create_task(dashboard_loop(state, ds, interval=0.05))
    await asyncio.sleep(0.22)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Should have ticked at least 3 times in 220ms at 50ms interval
    assert tick_count >= 3


@pytest.mark.asyncio
async def test_dashboard_loop_handles_empty_feeds():
    state = AppState()  # no feed data
    ds = DashboardState()
    task = asyncio.create_task(dashboard_loop(state, ds, interval=0.05))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Should not crash — empty dicts are fine
    assert isinstance(ds.spot_prices, dict)


@pytest.mark.asyncio
async def test_dashboard_loop_copies_positions():
    from unittest.mock import AsyncMock, MagicMock
    from dataclasses import dataclass

    @dataclass
    class FakePos:
        token_id: str
        group_key: str
        side: str
        size_usdc: float
        entry_price: float

    state = _make_app_state_with_feeds()
    risk = MagicMock()
    risk._lock = asyncio.Lock()
    risk.open_positions = {"tok1": FakePos("tok1", "btc_above", "BUY_YES", 50.0, 0.30)}

    ds = DashboardState()
    task = asyncio.create_task(dashboard_loop(state, ds, risk=risk, interval=0.05))
    await asyncio.sleep(0.15)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(ds.positions) == 1
    assert ds.positions[0]["size_usdc"] == 50.0
    assert ds.positions[0]["entry_price"] == 0.30
    assert ds.positions[0]["side"] == "BUY_YES"


@pytest.mark.asyncio
async def test_dashboard_loop_copies_buy_no_positions_with_correct_pnl():
    from unittest.mock import MagicMock
    from dataclasses import dataclass

    @dataclass
    class FakePos:
        token_id: str
        group_key: str
        side: str
        size_usdc: float
        entry_price: float

    state = _make_app_state_with_feeds()
    state.markets["tok1"] = ContractState(
        yes_token_id="tok1",
        no_token_id="tok1_no",
        question="Will BTC stay below $80k?",
        category="crypto",
        best_bid=0.38,
        best_ask=0.42,
        volume_usd=10000.0,
    )
    risk = MagicMock()
    risk._lock = asyncio.Lock()
    risk.open_positions = {"tok1": FakePos("tok1", "btc_below", "BUY_NO", 50.0, 0.60)}

    ds = DashboardState()
    task = asyncio.create_task(dashboard_loop(state, ds, risk=risk, interval=0.05))
    await asyncio.sleep(0.15)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(ds.positions) == 1
    assert ds.positions[0]["side"] == "BUY_NO"
    assert ds.positions[0]["current_mid"] == pytest.approx(0.40)
    assert ds.positions[0]["pnl_usdc"] == pytest.approx(0.0)
