import asyncio
import pytest
from maker.order_manager import OrderManager
from maker.state import MakerState
from maker.types import QuoteIntent, CancelAll


class FakeClobClient:
    """Records calls instead of hitting the network."""
    def __init__(self):
        self.posted: list[dict] = []
        self.cancelled: list[str] = []
        self._next_id = 0

    def create_order(self, order_args):
        return {"id": f"signed-{self._next_id}"}

    def post_order(self, signed_order, orderType=None, post_only=False):
        self._next_id += 1
        oid = f"order-{self._next_id}"
        self.posted.append({
            "orderID": oid,
            "post_only": post_only,
            "orderType": str(orderType),
        })
        return {"orderID": oid, "status": "live"}

    def cancel_orders(self, order_ids):
        self.cancelled.extend(order_ids)
        return {"cancelled": order_ids}

    def cancel_all(self):
        self.cancelled.append("ALL")
        return {}


def test_place_new_quotes():
    maker_state = MakerState()
    clob = FakeClobClient()
    om = OrderManager(maker_state, clob=clob, paper=False)

    qi = QuoteIntent("tok1", 0.45, 0.55, 10.0, 10.0, "new_market")
    om.handle_quote_intent_sync(qi)

    assert len(clob.posted) == 2  # bid + ask
    assert clob.posted[0]["post_only"] is True
    assert "tok1" in maker_state.live_orders


def test_replace_existing_quotes():
    maker_state = MakerState()
    clob = FakeClobClient()
    om = OrderManager(maker_state, clob=clob, paper=False)

    qi1 = QuoteIntent("tok1", 0.45, 0.55, 10.0, 10.0, "new_market")
    om.handle_quote_intent_sync(qi1)

    qi2 = QuoteIntent("tok1", 0.46, 0.56, 10.0, 10.0, "reprice")
    om.handle_quote_intent_sync(qi2)

    assert len(clob.cancelled) == 2  # old bid + ask cancelled
    assert len(clob.posted) == 4     # 2 old + 2 new


def test_cancel_single_market():
    maker_state = MakerState()
    clob = FakeClobClient()
    om = OrderManager(maker_state, clob=clob, paper=False)

    qi = QuoteIntent("tok1", 0.45, 0.55, 10.0, 10.0, "new_market")
    om.handle_quote_intent_sync(qi)

    om.handle_cancel_sync(CancelAll("tok1"))
    assert "tok1" not in maker_state.live_orders


def test_cancel_all_global():
    maker_state = MakerState()
    clob = FakeClobClient()
    om = OrderManager(maker_state, clob=clob, paper=False)

    for i in range(3):
        qi = QuoteIntent(f"tok{i}", 0.45, 0.55, 10.0, 10.0, "new_market")
        om.handle_quote_intent_sync(qi)

    om.handle_cancel_sync(CancelAll("*"))
    assert len(maker_state.live_orders) == 0
    assert "ALL" in clob.cancelled


def test_paper_mode_no_api_calls():
    maker_state = MakerState()
    clob = FakeClobClient()
    om = OrderManager(maker_state, clob=clob, paper=True)

    qi = QuoteIntent("tok1", 0.45, 0.55, 10.0, 10.0, "new_market")
    om.handle_quote_intent_sync(qi)

    assert len(clob.posted) == 0  # no API calls in paper mode
    assert "tok1" in maker_state.live_orders  # but state is tracked
