import pytest
from trading.slippage import estimate_slippage, SlippageEstimate


def test_thin_market_not_tradeable():
    est = estimate_slippage("BUY", 50.0, best_bid=0.48, best_ask=0.52, volume_usd=500)
    assert est.tradeable is False


def test_adequate_market_tradeable():
    est = estimate_slippage("BUY", 50.0, best_bid=0.48, best_ask=0.52, volume_usd=100_000)
    assert est.tradeable is True


def test_buy_adjusted_price_above_ask():
    est = estimate_slippage("BUY", 100.0, best_bid=0.48, best_ask=0.52, volume_usd=100_000)
    assert est.adjusted_price >= 0.52


def test_sell_adjusted_price_below_bid():
    est = estimate_slippage("SELL", 100.0, best_bid=0.48, best_ask=0.52, volume_usd=100_000)
    assert est.adjusted_price <= 0.48


def test_large_order_has_more_slippage():
    small = estimate_slippage("BUY", 10.0,  best_bid=0.48, best_ask=0.52, volume_usd=50_000)
    large = estimate_slippage("BUY", 500.0, best_bid=0.48, best_ask=0.52, volume_usd=50_000)
    assert large.slippage_pct > small.slippage_pct


def test_slippage_capped_at_10_pct():
    est = estimate_slippage("BUY", 99999.0, best_bid=0.48, best_ask=0.52, volume_usd=15_000)
    assert est.slippage_pct <= 0.10


def test_zero_volume_not_tradeable():
    est = estimate_slippage("BUY", 50.0, best_bid=0.48, best_ask=0.52, volume_usd=0)
    assert est.tradeable is False


def test_slippage_estimate_fields():
    est = estimate_slippage("BUY", 50.0, best_bid=0.48, best_ask=0.52, volume_usd=100_000)
    assert hasattr(est, "adjusted_price")
    assert hasattr(est, "slippage_pct")
    assert hasattr(est, "tradeable")
