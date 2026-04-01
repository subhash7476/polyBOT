import asyncio
from maker.state import MakerState


def test_initial_state():
    s = MakerState()
    assert s.inventory == {}
    assert s.live_orders == {}
    assert s.last_quotes == {}
    assert s.cooldowns == {}
    assert s.daily_pnl == 0.0


def test_get_inventory_default():
    s = MakerState()
    assert s.get_inventory("abc") == 0.0


def test_update_inventory_buy():
    s = MakerState()
    s.update_inventory("abc", "BUY", 10.0)
    assert s.get_inventory("abc") == 10.0


def test_update_inventory_sell():
    s = MakerState()
    s.update_inventory("abc", "BUY", 10.0)
    s.update_inventory("abc", "SELL", 4.0)
    assert s.get_inventory("abc") == 6.0


def test_total_inventory():
    s = MakerState()
    s.update_inventory("a", "BUY", 10.0)
    s.update_inventory("b", "SELL", 5.0)
    assert s.total_abs_inventory == 15.0


def test_skew_factor_zero_when_empty():
    s = MakerState()
    assert s.skew_factor("abc") == 0.0


def test_skew_factor_clamped():
    s = MakerState(max_inventory_per_market=50.0)
    s.update_inventory("abc", "BUY", 100.0)  # exceeds cap
    assert s.skew_factor("abc") == 1.0


def test_is_cooled_down():
    s = MakerState()
    s.cooldowns["abc"] = 9999999999.0  # far future
    assert s.is_cooled_down("abc")


def test_not_cooled_down():
    s = MakerState()
    s.cooldowns["abc"] = 0.0  # in the past
    assert not s.is_cooled_down("abc")
