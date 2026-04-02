import pytest
from maker.quote_engine import QuoteEngine, compute_fair_value, compute_spread
from maker.types import LadderUpdate, QuoteIntent


def test_fair_value_at_midpoint_no_skew():
    fv = compute_fair_value(mid=0.50, skew=0.0, model_adj=0.0)
    assert fv == 0.50


def test_fair_value_with_positive_skew():
    fv = compute_fair_value(mid=0.50, skew=0.5, model_adj=0.0)
    assert fv > 0.50
    assert fv == 0.515


def test_fair_value_clamped():
    fv = compute_fair_value(mid=0.98, skew=1.0, model_adj=0.0)
    assert fv <= 0.95


def test_spread_base():
    s = compute_spread(volume_usd=1000.0, abs_inventory=0.0, hours_to_expiry=100.0)
    assert s == 0.06


def test_spread_widens_low_volume():
    s = compute_spread(volume_usd=200.0, abs_inventory=0.0, hours_to_expiry=100.0)
    assert s == 0.08


def test_spread_widens_with_inventory():
    s = compute_spread(volume_usd=1000.0, abs_inventory=10.0, hours_to_expiry=100.0)
    assert s == 0.16


def test_spread_widens_near_expiry():
    s = compute_spread(volume_usd=1000.0, abs_inventory=0.0, hours_to_expiry=24.0)
    assert s == 0.09


def test_spread_max_very_near_expiry():
    s = compute_spread(volume_usd=1000.0, abs_inventory=0.0, hours_to_expiry=3.0)
    assert s == 0.15


def test_spread_floor():
    s = compute_spread(volume_usd=10000.0, abs_inventory=0.0, hours_to_expiry=500.0)
    assert s >= 0.04


def test_build_ladder_returns_correct_number_of_levels():
    lu = QuoteEngine.build_ladder(
        token_id="abc",
        fair_value=0.50,
        spread=0.06,
        size=10.0,
        reason="reprice",
    )
    assert isinstance(lu, LadderUpdate)
    assert len(lu.levels) == 3  # LADDER_LEVELS


def test_build_ladder_center_level():
    """Center level (index 1) should be centered on fair value."""
    lu = QuoteEngine.build_ladder(
        token_id="abc",
        fair_value=0.50,
        spread=0.06,
        size=10.0,
        reason="reprice",
    )
    center = lu.center
    assert center.bid_price == round(0.50 - 0.03, 4)   # fv - half_spread
    assert center.ask_price == round(0.50 + 0.03, 4)


def test_build_ladder_outer_levels_wider():
    """Outer levels are offset by LEVEL_STEP from center."""
    lu = QuoteEngine.build_ladder(
        token_id="abc",
        fair_value=0.50,
        spread=0.06,
        size=10.0,
        reason="reprice",
    )
    inner = lu.levels[1]   # center
    outer = lu.levels[0]   # one step below center
    assert outer.bid_price < inner.bid_price
    assert outer.ask_price > inner.ask_price


def test_build_ladder_token_id_on_all_levels():
    lu = QuoteEngine.build_ladder("tok1", 0.50, 0.06, 10.0, "reprice")
    assert all(qi.token_id == "tok1" for qi in lu.levels)


def test_is_stale_returns_false_when_same():
    old = QuoteIntent("abc", 0.45, 0.55, 10.0, 10.0, "reprice")
    new = QuoteIntent("abc", 0.45, 0.55, 10.0, 10.0, "reprice")
    assert not QuoteEngine.is_stale(old, new, tick=0.01)


def test_is_stale_returns_true_when_price_changed():
    old = QuoteIntent("abc", 0.45, 0.55, 10.0, 10.0, "reprice")
    new = QuoteIntent("abc", 0.47, 0.57, 10.0, 10.0, "reprice")
    assert QuoteEngine.is_stale(old, new, tick=0.01)
