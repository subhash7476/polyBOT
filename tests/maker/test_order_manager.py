import asyncio
import pytest
from maker.order_manager import OrderManager
from maker.state import MakerState
from maker.types import LadderUpdate, QuoteIntent, CancelAll


class FakeClobClient:
    def __init__(self):
        self.posted: list[dict] = []
        self.cancelled: list[str] = []
        self._next_id = 0

    def create_order(self, order_args):
        return {"id": f"signed-{self._next_id}"}

    def post_order(self, signed_order, orderType=None, post_only=False):
        self._next_id += 1
        oid = f"order-{self._next_id}"
        self.posted.append({"orderID": oid, "post_only": post_only})
        return {"orderID": oid, "status": "live"}

    def cancel_orders(self, order_ids):
        self.cancelled.extend(order_ids)

    def cancel_all(self):
        self.cancelled.append("ALL")


def _make_ladder(token_id: str, n_levels: int = 3) -> LadderUpdate:
    levels = [
        QuoteIntent(token_id, round(0.45 - i * 0.01, 4), round(0.55 + i * 0.01, 4), 10.0, 10.0, "reprice")
        for i in range(n_levels)
    ]
    return LadderUpdate(token_id=token_id, levels=levels, reason="reprice")


def test_place_new_ladder():
    """First ladder for a market places 2 orders per level (bid + ask)."""
    maker_state = MakerState()
    clob = FakeClobClient()
    om = OrderManager(maker_state, clob=clob, paper=False)

    om.handle_ladder_sync(_make_ladder("tok1", n_levels=3))

    assert len(clob.posted) == 6        # 3 levels × 2 orders each
    assert all(o["post_only"] for o in clob.posted)
    assert "tok1" in maker_state.live_orders
    assert len(maker_state.live_orders["tok1"]) == 3


def test_replace_existing_ladder():
    """Re-pricing cancels all old levels and places fresh ones."""
    maker_state = MakerState()
    clob = FakeClobClient()
    om = OrderManager(maker_state, clob=clob, paper=False)

    om.handle_ladder_sync(_make_ladder("tok1", n_levels=3))   # 6 placed
    om.handle_ladder_sync(_make_ladder("tok1", n_levels=3))   # 6 cancelled + 6 new

    assert len(clob.cancelled) == 6     # bid+ask for each of the 3 old levels
    assert len(clob.posted) == 12       # 6 old + 6 new


def test_cancel_single_market():
    maker_state = MakerState()
    clob = FakeClobClient()
    om = OrderManager(maker_state, clob=clob, paper=False)

    om.handle_ladder_sync(_make_ladder("tok1"))
    om.handle_cancel_sync(CancelAll("tok1"))

    assert "tok1" not in maker_state.live_orders
    assert len(clob.cancelled) == 6     # all 3 levels cancelled


def test_cancel_all_global():
    maker_state = MakerState()
    clob = FakeClobClient()
    om = OrderManager(maker_state, clob=clob, paper=False)

    for i in range(3):
        om.handle_ladder_sync(_make_ladder(f"tok{i}"))

    om.handle_cancel_sync(CancelAll("*"))
    assert len(maker_state.live_orders) == 0
    assert "ALL" in clob.cancelled


def test_paper_mode_no_api_calls():
    maker_state = MakerState()
    clob = FakeClobClient()
    om = OrderManager(maker_state, clob=clob, paper=True)

    om.handle_ladder_sync(_make_ladder("tok1"))

    assert len(clob.posted) == 0
    assert "tok1" in maker_state.live_orders
    assert len(maker_state.live_orders["tok1"]) == 3
