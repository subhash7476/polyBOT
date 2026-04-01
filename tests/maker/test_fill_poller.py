import asyncio
import time
import pytest
from maker.fill_poller import FillPoller
from maker.state import MakerState
from market.state import ContractState


def _make_market(bid: float = 0.50, ask: float = 0.60) -> ContractState:
    return ContractState(
        yes_token_id="tok1",
        no_token_id="no-tok1",
        question="test",
        category="sports",
        best_bid=bid,
        best_ask=ask,
    )


def test_paper_fill_bid_crossed():
    """When market ask drops below our bid, our bid fills."""
    maker_state = MakerState()

    maker_state.live_orders["tok1"] = {
        "bid_order_id": "paper-bid",
        "ask_order_id": "paper-ask",
        "bid_price": 0.50,
        "ask_price": 0.60,
        "bid_size": 10.0,
        "ask_size": 10.0,
    }

    # Market ask dropped to 0.48 — below our bid of 0.50
    markets = {"tok1": _make_market(bid=0.45, ask=0.48)}

    fills = FillPoller.check_paper_fills(maker_state, markets)
    assert len(fills) == 1
    assert fills[0].side == "BUY"
    assert fills[0].price == 0.50


def test_paper_fill_ask_crossed():
    """When market bid rises above our ask, our ask fills."""
    maker_state = MakerState()

    maker_state.live_orders["tok1"] = {
        "bid_order_id": "paper-bid",
        "ask_order_id": "paper-ask",
        "bid_price": 0.50,
        "ask_price": 0.60,
        "bid_size": 10.0,
        "ask_size": 10.0,
    }

    # Market bid rose to 0.62 — above our ask of 0.60
    markets = {"tok1": _make_market(bid=0.62, ask=0.65)}

    fills = FillPoller.check_paper_fills(maker_state, markets)
    assert len(fills) == 1
    assert fills[0].side == "SELL"
    assert fills[0].price == 0.60


def test_paper_no_fill_when_not_crossed():
    maker_state = MakerState()

    maker_state.live_orders["tok1"] = {
        "bid_order_id": "paper-bid",
        "ask_order_id": "paper-ask",
        "bid_price": 0.50,
        "ask_price": 0.60,
        "bid_size": 10.0,
        "ask_size": 10.0,
    }

    # Market sits between our quotes — no fill
    markets = {"tok1": _make_market(bid=0.52, ask=0.58)}

    fills = FillPoller.check_paper_fills(maker_state, markets)
    assert len(fills) == 0
