from maker.types import QuoteIntent, Fill, SkewUpdate, CancelAll, LadderUpdate


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
    assert f.notional == 4.5


def test_skew_update():
    s = SkewUpdate(token_id="abc123", skew_factor=0.5)
    assert s.skew_factor == 0.5


def test_cancel_all_single():
    c = CancelAll(token_id="abc123")
    assert not c.is_global


def test_cancel_all_global():
    c = CancelAll(token_id="*")
    assert c.is_global


def test_ladder_update_levels():
    levels = [
        QuoteIntent("tok1", 0.44, 0.56, 10.0, 10.0, "reprice"),
        QuoteIntent("tok1", 0.45, 0.55, 10.0, 10.0, "reprice"),
        QuoteIntent("tok1", 0.46, 0.54, 10.0, 10.0, "reprice"),
    ]
    lu = LadderUpdate(token_id="tok1", levels=levels, reason="reprice")
    assert lu.token_id == "tok1"
    assert len(lu.levels) == 3
    assert lu.levels[0].bid_price == 0.44
    assert lu.reason == "reprice"


def test_ladder_update_center():
    """center property returns the middle level."""
    levels = [
        QuoteIntent("tok1", 0.44, 0.56, 10.0, 10.0, "reprice"),
        QuoteIntent("tok1", 0.45, 0.55, 10.0, 10.0, "reprice"),
        QuoteIntent("tok1", 0.46, 0.54, 10.0, 10.0, "reprice"),
    ]
    lu = LadderUpdate(token_id="tok1", levels=levels, reason="reprice")
    assert lu.center.bid_price == 0.45


def test_ladder_update_is_frozen():
    """LadderUpdate is a frozen dataclass — field reassignment must raise FrozenInstanceError."""
    from dataclasses import FrozenInstanceError
    import pytest
    levels = [QuoteIntent("tok1", 0.44, 0.56, 10.0, 10.0, "reprice")]
    lu = LadderUpdate(token_id="tok1", levels=levels, reason="reprice")
    with pytest.raises(FrozenInstanceError):
        lu.token_id = "other"
