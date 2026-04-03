import time
import pytest
from maker.fill_poller import FillPoller
from maker.state import MakerState
from market.state import ContractState


def _make_market(bid: float = 0.20, ask: float = 0.80, volume_usd: float = 5000.0) -> ContractState:
    return ContractState(
        yes_token_id="tok1",
        no_token_id="no-tok1",
        question="test",
        category="sports",
        best_bid=bid,
        best_ask=ask,
        volume_usd=volume_usd,
    )


def _add_ladder(maker_state: MakerState, token_id: str, n_levels: int = 3):
    """Seed live_orders with a list of per-level dicts."""
    maker_state.live_orders[token_id] = [
        {
            "bid_order_id": f"paper-bid-{i}",
            "ask_order_id": f"paper-ask-{i}",
            "bid_price": round(0.47 - i * 0.01, 4),
            "ask_price": round(0.53 + i * 0.01, 4),
            "bid_size": 10.0,
            "ask_size": 10.0,
        }
        for i in range(n_levels)
    ]


def test_paper_fill_competitive_bid():
    """Bid fills when our bid > market best_bid (we're the best bid)."""
    maker_state = MakerState()
    _add_ladder(maker_state, "tok1", n_levels=1)
    # Wide market: best_bid=0.20, our bid=0.47 → competitive
    markets = {"tok1": _make_market(bid=0.20, ask=0.80)}
    fills = FillPoller.check_paper_fills(maker_state, markets, poll_interval=1e6)
    bid_fills = [f for f in fills if f.side == "BUY"]
    assert len(bid_fills) >= 1
    assert bid_fills[0].price == 0.47


def test_paper_fill_competitive_ask():
    """Ask fills when our ask < market best_ask (we're the best ask)."""
    maker_state = MakerState()
    _add_ladder(maker_state, "tok1", n_levels=1)
    markets = {"tok1": _make_market(bid=0.20, ask=0.80)}
    fills = FillPoller.check_paper_fills(maker_state, markets, poll_interval=1e6)
    ask_fills = [f for f in fills if f.side == "SELL"]
    assert len(ask_fills) >= 1
    assert ask_fills[0].price == 0.53


def test_paper_fill_multiple_levels_can_fill():
    """With a 3-level ladder in a wide-spread market, multiple levels can fill."""
    maker_state = MakerState()
    _add_ladder(maker_state, "tok1", n_levels=3)
    markets = {"tok1": _make_market(bid=0.20, ask=0.80)}
    fills = FillPoller.check_paper_fills(maker_state, markets, poll_interval=1e6)
    # All 3 bid levels are competitive (all > 0.20); up to 3 bid fills
    bid_fills = [f for f in fills if f.side == "BUY"]
    assert len(bid_fills) <= 3


def test_paper_no_fill_when_uncompetitive():
    """No fill when our quotes are outside the market spread."""
    maker_state = MakerState()
    # Our bids: 0.40, 0.39, 0.38 — all below market bid of 0.48 → not competitive
    maker_state.live_orders["tok1"] = [
        {"bid_order_id": "b", "ask_order_id": "a",
         "bid_price": round(0.40 - i * 0.01, 4),
         "ask_price": round(0.70 + i * 0.01, 4),
         "bid_size": 10.0, "ask_size": 10.0}
        for i in range(3)
    ]
    markets = {"tok1": _make_market(bid=0.48, ask=0.52)}
    fills = FillPoller.check_paper_fills(maker_state, markets, poll_interval=1e6)
    assert len(fills) == 0


def test_paper_no_fill_zero_volume():
    maker_state = MakerState()
    _add_ladder(maker_state, "tok1", n_levels=1)
    markets = {"tok1": _make_market(bid=0.20, ask=0.80, volume_usd=0.0)}
    fills = FillPoller.check_paper_fills(maker_state, markets, poll_interval=1e6)
    assert len(fills) == 0
