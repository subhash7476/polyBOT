from maker.quote_engine import QuoteEngine, compute_fair_value, compute_spread
from maker.types import QuoteIntent


def test_fair_value_at_midpoint_no_skew():
    fv = compute_fair_value(mid=0.50, skew=0.0, model_adj=0.0)
    assert fv == 0.50


def test_fair_value_with_positive_skew():
    """Holding YES → push fair value up to attract sellers."""
    fv = compute_fair_value(mid=0.50, skew=0.5, model_adj=0.0)
    assert fv > 0.50
    assert fv == 0.515  # 0.50 + 0.5 * 0.03


def test_fair_value_clamped():
    fv = compute_fair_value(mid=0.98, skew=1.0, model_adj=0.0)
    assert fv <= 0.95


def test_spread_base():
    s = compute_spread(volume_usd=1000.0, abs_inventory=0.0, hours_to_expiry=100.0)
    assert s == 0.06  # BASE_SPREAD


def test_spread_widens_low_volume():
    s = compute_spread(volume_usd=200.0, abs_inventory=0.0, hours_to_expiry=100.0)
    assert s == 0.08  # 0.06 + 0.02


def test_spread_widens_with_inventory():
    s = compute_spread(volume_usd=1000.0, abs_inventory=10.0, hours_to_expiry=100.0)
    assert s == 0.16  # 0.06 + 10 * 0.01


def test_spread_widens_near_expiry():
    s = compute_spread(volume_usd=1000.0, abs_inventory=0.0, hours_to_expiry=24.0)
    assert s == 0.09  # 0.06 + 0.03


def test_spread_max_very_near_expiry():
    s = compute_spread(volume_usd=1000.0, abs_inventory=0.0, hours_to_expiry=3.0)
    assert s == 0.15  # MAX_SPREAD


def test_spread_floor():
    s = compute_spread(volume_usd=10000.0, abs_inventory=0.0, hours_to_expiry=500.0)
    assert s >= 0.04  # MIN_SPREAD


def test_build_quote_intent():
    qi = QuoteEngine.build_quote(
        token_id="abc",
        fair_value=0.50,
        spread=0.10,
        bid_size=10.0,
        ask_size=10.0,
        reason="reprice",
    )
    assert qi.bid_price == 0.45
    assert qi.ask_price == 0.55
    assert qi.spread == 0.10


def test_is_stale_returns_false_when_same():
    old = QuoteIntent("abc", 0.45, 0.55, 10.0, 10.0, "reprice")
    new = QuoteIntent("abc", 0.45, 0.55, 10.0, 10.0, "reprice")
    assert not QuoteEngine.is_stale(old, new, tick=0.01)


def test_is_stale_returns_true_when_price_changed():
    old = QuoteIntent("abc", 0.45, 0.55, 10.0, 10.0, "reprice")
    new = QuoteIntent("abc", 0.47, 0.57, 10.0, 10.0, "reprice")
    assert QuoteEngine.is_stale(old, new, tick=0.01)
