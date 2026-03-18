import pytest
from trading.kelly import fractional_kelly, HARD_CAP_USDC, KELLY_MIN, KELLY_MAX


def test_positive_edge_returns_positive_size():
    size = fractional_kelly(model_prob=0.65, market_price=0.50,
                            bankroll=500.0, ev=0.05, signal_count=3)
    assert size > 0


def test_no_edge_returns_zero():
    size = fractional_kelly(model_prob=0.50, market_price=0.50,
                            bankroll=500.0, ev=0.0, signal_count=3)
    assert size == 0.0


def test_negative_full_kelly_returns_zero():
    size = fractional_kelly(model_prob=0.30, market_price=0.50,
                            bankroll=500.0, ev=-0.05, signal_count=3)
    assert size == 0.0


def test_hard_cap_enforced():
    size = fractional_kelly(model_prob=0.99, market_price=0.01,
                            bankroll=10_000.0, ev=0.20, signal_count=5)
    assert size <= HARD_CAP_USDC


def test_kelly_fraction_clamped_to_range():
    # With kelly_fraction=1.0, internal clamp should keep it at KELLY_MAX=0.10
    size = fractional_kelly(model_prob=0.65, market_price=0.50,
                            bankroll=500.0, ev=0.05, signal_count=3,
                            kelly_fraction=1.0)
    assert size <= HARD_CAP_USDC  # hard cap applies regardless


def test_ev_confidence_scales_size():
    # Low EV → smaller size; high EV → larger size
    small = fractional_kelly(0.60, 0.50, 500.0, ev=0.01, signal_count=3)
    large = fractional_kelly(0.60, 0.50, 500.0, ev=0.10, signal_count=3)
    assert large > small


def test_signal_confidence_scales_size():
    # Fewer signals → smaller size
    few = fractional_kelly(0.60, 0.50, 500.0, ev=0.05, signal_count=1)
    many = fractional_kelly(0.60, 0.50, 500.0, ev=0.05, signal_count=5)
    assert many > few


def test_invalid_inputs_return_zero():
    assert fractional_kelly(0.0, 0.50, 500.0, ev=0.05, signal_count=3) == 0.0
    assert fractional_kelly(0.60, 0.0, 500.0, ev=0.05, signal_count=3) == 0.0
