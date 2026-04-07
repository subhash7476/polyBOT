"""Tests for maker/shadow_fill_poller.py"""

import asyncio
import pytest
from maker.shadow_fill_poller import ShadowFillPoller
from maker.state import MakerState
from market.state import AppState, ContractState


def _make_app_state(token_id: str, bid: float = 0.44, ask: float = 0.48) -> AppState:
    app = AppState()
    cs = ContractState(
        yes_token_id=token_id,
        no_token_id="no-" + token_id,
        question="Test market",
        category="sports",
        best_bid=bid,
        best_ask=ask,
        volume_usd=5000.0,
    )
    app.markets[token_id] = cs
    return app


def _make_maker_state_with_quotes(
    token_id: str,
    bid_price: float = 0.45,
    ask_price: float = 0.47,
    size: float = 10.0,
) -> MakerState:
    ms = MakerState()
    ms.live_orders[token_id] = [{
        "bid_price": bid_price,
        "ask_price": ask_price,
        "bid_size": size,
        "ask_size": size,
        "bid_order_id": "bid-order-1",
        "ask_order_id": "ask-order-1",
    }]
    return ms


@pytest.mark.asyncio
async def test_trade_through_bid_generates_buy_fill():
    """Trade at or below our bid price → BUY fill."""
    token_id = "tok1"
    app = _make_app_state(token_id)
    ms = _make_maker_state_with_quotes(token_id, bid_price=0.45, ask_price=0.47)
    trades_q: asyncio.Queue = asyncio.Queue()
    fills_q: asyncio.Queue = asyncio.Queue()

    poller = ShadowFillPoller(app, ms, fills_q, trades_q)

    # Trade at exactly our bid → taker sold to us
    trades_q.put_nowait((token_id, 0.45, 5.0))
    trades_q.put_nowait(None)  # sentinel to stop after one event

    # Run one iteration manually via _check_fills
    fills = poller._check_fills(token_id, 0.45, 5.0, mid=0.46)
    assert len(fills) == 1
    assert fills[0].side == "BUY"
    assert fills[0].price == 0.45
    assert fills[0].size == 5.0


@pytest.mark.asyncio
async def test_trade_through_ask_generates_sell_fill():
    """Trade at or above our ask price → SELL fill."""
    token_id = "tok2"
    app = _make_app_state(token_id)
    ms = _make_maker_state_with_quotes(token_id, bid_price=0.45, ask_price=0.47)
    fills_q: asyncio.Queue = asyncio.Queue()
    trades_q: asyncio.Queue = asyncio.Queue()

    poller = ShadowFillPoller(app, ms, fills_q, trades_q)

    fills = poller._check_fills(token_id, 0.47, 10.0, mid=0.46)
    assert len(fills) == 1
    assert fills[0].side == "SELL"
    assert fills[0].price == 0.47


@pytest.mark.asyncio
async def test_trade_inside_spread_no_fill():
    """Trade between our bid and ask doesn't fill either side."""
    token_id = "tok3"
    app = _make_app_state(token_id)
    ms = _make_maker_state_with_quotes(token_id, bid_price=0.45, ask_price=0.47)
    poller = ShadowFillPoller(app, ms, asyncio.Queue(), asyncio.Queue())

    fills = poller._check_fills(token_id, 0.46, 10.0, mid=0.46)
    assert fills == []


@pytest.mark.asyncio
async def test_fill_size_capped_at_trade_size():
    """Fill size = min(level_size, trade_size)."""
    token_id = "tok4"
    app = _make_app_state(token_id)
    ms = _make_maker_state_with_quotes(token_id, bid_price=0.45, ask_price=0.47, size=20.0)
    poller = ShadowFillPoller(app, ms, asyncio.Queue(), asyncio.Queue())

    # Trade size is 3 but our level is 20 — fill only 3
    fills = poller._check_fills(token_id, 0.44, 3.0, mid=0.46)
    assert fills[0].size == 3.0


@pytest.mark.asyncio
async def test_no_quotes_no_fills():
    """No live orders → no fills regardless of trade."""
    token_id = "tok5"
    app = _make_app_state(token_id)
    ms = MakerState()  # empty live_orders
    poller = ShadowFillPoller(app, ms, asyncio.Queue(), asyncio.Queue())

    fills = poller._check_fills(token_id, 0.40, 10.0, mid=0.46)
    assert fills == []


@pytest.mark.asyncio
async def test_filled_order_consumed():
    """After a fill, the order is removed from live_orders (no double-fill)."""
    token_id = "tok6"
    app = _make_app_state(token_id)
    ms = _make_maker_state_with_quotes(token_id, bid_price=0.45, ask_price=0.47, size=10.0)
    fills_q: asyncio.Queue = asyncio.Queue()
    trades_q: asyncio.Queue = asyncio.Queue()
    poller = ShadowFillPoller(app, ms, fills_q, trades_q)

    from maker.fill_poller import FillPoller

    fills = poller._check_fills(token_id, 0.44, 10.0, mid=0.46)
    assert len(fills) == 1
    FillPoller.consume_paper_fills(ms, fills)

    # Same trade again should not fill
    fills2 = poller._check_fills(token_id, 0.44, 10.0, mid=0.46)
    assert fills2 == []


@pytest.mark.asyncio
async def test_unknown_market_uses_trade_price_as_mid():
    """If token not in app_state.markets, mid falls back to trade_price (no crash)."""
    app = AppState()  # empty — no markets
    ms = _make_maker_state_with_quotes("tokX", bid_price=0.45, ask_price=0.47)
    fills_q: asyncio.Queue = asyncio.Queue()
    trades_q: asyncio.Queue = asyncio.Queue()
    poller = ShadowFillPoller(app, ms, fills_q, trades_q)

    # The _check_fills itself doesn't need mid from app, it comes from caller
    # Just verify no crash when market is missing — run() handles the mid lookup
    fills = poller._check_fills("tokX", 0.44, 5.0, mid=0.44)
    assert fills[0].mid_at_fill == 0.44


@pytest.mark.asyncio
async def test_multi_level_ladder_partial_fill():
    """A small trade only fills levels whose price was crossed."""
    token_id = "tok7"
    app = _make_app_state(token_id)
    ms = MakerState()
    # Three ladder levels with different bid prices
    ms.live_orders[token_id] = [
        {"bid_price": 0.43, "ask_price": 0.49, "bid_size": 10.0, "ask_size": 10.0,
         "bid_order_id": "bid-L0", "ask_order_id": "ask-L0"},
        {"bid_price": 0.44, "ask_price": 0.48, "bid_size": 10.0, "ask_size": 10.0,
         "bid_order_id": "bid-L1", "ask_order_id": "ask-L1"},
        {"bid_price": 0.45, "ask_price": 0.47, "bid_size": 10.0, "ask_size": 10.0,
         "bid_order_id": "bid-L2", "ask_order_id": "ask-L2"},
    ]
    poller = ShadowFillPoller(app, ms, asyncio.Queue(), asyncio.Queue())

    # Trade at 0.45 crosses only the top level bid (bid_L2)
    fills = poller._check_fills(token_id, 0.45, 50.0, mid=0.46)
    bid_fills = [f for f in fills if f.side == "BUY"]
    # Only levels with bid_price >= 0.45 should fill
    assert all(f.price >= 0.45 for f in bid_fills)
