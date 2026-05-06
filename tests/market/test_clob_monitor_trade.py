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
