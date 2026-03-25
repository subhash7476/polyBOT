import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from trading.executor import should_cancel_stale_order, split_order_sizes


def test_split_order_sizes_below_threshold():
    # Orders <= $30 are not split
    sizes = split_order_sizes(25.0)
    assert sizes == [25.0]


def test_split_order_sizes_above_threshold():
    # Orders > $30 split into 2-3 smaller orders
    sizes = split_order_sizes(45.0)
    assert len(sizes) >= 2
    assert abs(sum(sizes) - 45.0) < 0.01


def test_split_order_sizes_capped():
    # Each split piece must not exceed MAX_TRADE_SIZE_USDC
    sizes = split_order_sizes(50.0)
    from config import MAX_TRADE_SIZE_USDC
    assert all(s <= MAX_TRADE_SIZE_USDC for s in sizes)


def test_should_cancel_stale_order_when_old_and_price_moved():
    result = should_cancel_stale_order(
        order_age_minutes=35,
        price_at_order=0.60,
        current_price=0.63,   # 3% move
    )
    assert result is True


def test_should_not_cancel_when_price_stable():
    result = should_cancel_stale_order(
        order_age_minutes=35,
        price_at_order=0.60,
        current_price=0.61,   # 1% move — within tolerance
    )
    assert result is False


def test_should_not_cancel_when_young():
    result = should_cancel_stale_order(
        order_age_minutes=10,  # only 10 min old
        price_at_order=0.60,
        current_price=0.65,   # big price move but order is young
    )
    assert result is False
