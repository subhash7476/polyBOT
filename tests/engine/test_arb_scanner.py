import pytest
from engine.arb_scanner import (
    ThresholdMarket, find_monotonicity_violations, ArbOpportunity
)


def test_no_violation_when_monotonic():
    markets = [
        ThresholdMarket(token_id="a", asset="BTC", target=80000, direction="above",
                        expiry_key="mar2026", yes_price=0.75, no_token_id="a_no"),
        ThresholdMarket(token_id="b", asset="BTC", target=85000, direction="above",
                        expiry_key="mar2026", yes_price=0.50, no_token_id="b_no"),
        ThresholdMarket(token_id="c", asset="BTC", target=90000, direction="above",
                        expiry_key="mar2026", yes_price=0.30, no_token_id="c_no"),
    ]
    violations = find_monotonicity_violations(markets)
    assert len(violations) == 0


def test_detects_violation():
    markets = [
        ThresholdMarket(token_id="a", asset="BTC", target=85000, direction="above",
                        expiry_key="mar2026", yes_price=0.30, no_token_id="a_no"),
        ThresholdMarket(token_id="b", asset="BTC", target=90000, direction="above",
                        expiry_key="mar2026", yes_price=0.30, no_token_id="b_no"),
    ]
    violations = find_monotonicity_violations(markets)
    assert len(violations) >= 1
    v = violations[0]
    assert v.low_strike_token == "a"   # BTC > $85k (should be more expensive)
    assert v.high_strike_token == "b"  # BTC > $90k


def test_violation_with_inverted_prices():
    """Higher strike priced ABOVE lower strike — clear violation."""
    markets = [
        ThresholdMarket(token_id="a", asset="BTC", target=85000, direction="above",
                        expiry_key="mar2026", yes_price=0.30, no_token_id="a_no"),
        ThresholdMarket(token_id="b", asset="BTC", target=90000, direction="above",
                        expiry_key="mar2026", yes_price=0.40, no_token_id="b_no"),
    ]
    violations = find_monotonicity_violations(markets)
    assert len(violations) >= 1


def test_groups_by_asset_and_expiry():
    """BTC March and BTC April should be separate groups."""
    markets = [
        ThresholdMarket(token_id="a", asset="BTC", target=85000, direction="above",
                        expiry_key="mar2026", yes_price=0.50, no_token_id="a_no"),
        ThresholdMarket(token_id="b", asset="BTC", target=90000, direction="above",
                        expiry_key="apr2026", yes_price=0.60, no_token_id="b_no"),
    ]
    # Different expiries — no violation possible across groups
    violations = find_monotonicity_violations(markets)
    assert len(violations) == 0


def test_minimum_spread_to_be_tradeable():
    """Monotonic prices — no violation regardless of min_spread."""
    markets = [
        ThresholdMarket(token_id="a", asset="BTC", target=85000, direction="above",
                        expiry_key="mar2026", yes_price=0.300, no_token_id="a_no"),
        ThresholdMarket(token_id="b", asset="BTC", target=90000, direction="above",
                        expiry_key="mar2026", yes_price=0.298, no_token_id="b_no"),
    ]
    # 0.300 > 0.298 — lower strike IS more expensive (correct monotonicity)
    violations = find_monotonicity_violations(markets, min_spread=0.03)
    assert len(violations) == 0


def test_inverted_below_min_spread_not_reported():
    """Inverted prices but spread < min_spread — not worth trading."""
    markets = [
        ThresholdMarket(token_id="a", asset="BTC", target=85000, direction="above",
                        expiry_key="mar2026", yes_price=0.298, no_token_id="a_no"),
        ThresholdMarket(token_id="b", asset="BTC", target=90000, direction="above",
                        expiry_key="mar2026", yes_price=0.300, no_token_id="b_no"),
    ]
    # Inverted: higher strike MORE expensive, but spread=0.002 < min_spread=0.03
    violations = find_monotonicity_violations(markets, min_spread=0.03)
    assert len(violations) == 0


def test_violation_sorted_best_first():
    """Violations sorted by spread descending (best opportunities first)."""
    markets = [
        ThresholdMarket(token_id="a", asset="BTC", target=80000, direction="above",
                        expiry_key="mar2026", yes_price=0.20, no_token_id="a_no"),
        ThresholdMarket(token_id="b", asset="BTC", target=85000, direction="above",
                        expiry_key="mar2026", yes_price=0.50, no_token_id="b_no"),
        ThresholdMarket(token_id="c", asset="BTC", target=90000, direction="above",
                        expiry_key="mar2026", yes_price=0.30, no_token_id="c_no"),
    ]
    violations = find_monotonicity_violations(markets)
    assert len(violations) >= 1
    # Best spread violation should be first
    spreads = [v.spread for v in violations]
    assert spreads == sorted(spreads, reverse=True)


def test_different_assets_not_compared():
    """BTC and SOL markets should not be compared."""
    markets = [
        ThresholdMarket(token_id="a", asset="BTC", target=85000, direction="above",
                        expiry_key="mar2026", yes_price=0.30, no_token_id="a_no"),
        ThresholdMarket(token_id="b", asset="SOL", target=85000, direction="above",
                        expiry_key="mar2026", yes_price=0.30, no_token_id="b_no"),
    ]
    # Same price, same target, same expiry but DIFFERENT asset — no cross-asset arb
    violations = find_monotonicity_violations(markets)
    assert len(violations) == 0
