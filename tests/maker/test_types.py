from maker.types import QuoteIntent, Fill, SkewUpdate, CancelAll


def test_quote_intent_fields():
    qi = QuoteIntent(
        token_id="abc123",
        bid_price=0.45,
        ask_price=0.55,
        bid_size=10.0,
        ask_size=10.0,
        reason="new_market",
    )
    assert qi.token_id == "abc123"
    assert qi.bid_price == 0.45
    assert qi.ask_price == 0.55
    assert qi.spread == 0.10


def test_fill_fields():
    f = Fill(
        token_id="abc123",
        side="BUY",
        price=0.45,
        size=10.0,
        order_id="order-1",
        filled_at=1000.0,
    )
    assert f.side == "BUY"
    assert f.notional == 10.0


def test_skew_update():
    s = SkewUpdate(token_id="abc123", skew_factor=0.5)
    assert s.skew_factor == 0.5


def test_cancel_all_single():
    c = CancelAll(token_id="abc123")
    assert not c.is_global


def test_cancel_all_global():
    c = CancelAll(token_id="*")
    assert c.is_global
