from engine.arb_scanner import (
    find_cross_temporal_violations,
    ThresholdMarket,
    CrossTemporalViolation,
)


def _market(token_id, yes_price, no_token_id="no1", asset="BTC",
            target=100000, direction="above", expiry_key="mar2026"):
    return ThresholdMarket(
        token_id=token_id, asset=asset, target=target,
        direction=direction, expiry_key=expiry_key,
        yes_price=yes_price, no_token_id=no_token_id,
    )


def test_no_cross_temporal_violation_when_consistent():
    mar = _market("tok1", yes_price=0.30, expiry_key="mar2026")
    jun = _market("tok2", yes_price=0.45, expiry_key="jun2026")
    violations = find_cross_temporal_violations([mar, jun], min_profit=0.01)
    assert violations == []


def test_cross_temporal_violation_detected():
    mar = _market("tok1", yes_price=0.55, expiry_key="mar2026")
    jun = _market("tok2", yes_price=0.40, expiry_key="jun2026")
    violations = find_cross_temporal_violations([mar, jun], min_profit=0.01)
    assert len(violations) >= 1
    v = violations[0]
    assert isinstance(v, CrossTemporalViolation)
    assert v.profit > 0.01


def test_cross_temporal_below_direction():
    mar = _market("tok1", yes_price=0.60, direction="below", expiry_key="mar2026")
    jun = _market("tok2", yes_price=0.45, direction="below", expiry_key="jun2026")
    violations = find_cross_temporal_violations([mar, jun], min_profit=0.01)
    assert violations == []


def test_cross_temporal_below_violation():
    mar = _market("tok1", yes_price=0.30, direction="below", expiry_key="mar2026")
    jun = _market("tok2", yes_price=0.55, direction="below", expiry_key="jun2026")
    violations = find_cross_temporal_violations([mar, jun], min_profit=0.01)
    assert len(violations) >= 1


def test_empty_input():
    assert find_cross_temporal_violations([], min_profit=0.01) == []


def test_single_market_no_violation():
    markets = [_market("tok1", yes_price=0.50)]
    assert find_cross_temporal_violations(markets, min_profit=0.01) == []


def test_profit_below_min_filtered_out():
    mar = _market("tok1", yes_price=0.45, expiry_key="mar2026")
    jun = _market("tok2", yes_price=0.40, expiry_key="jun2026")
    violations = find_cross_temporal_violations([mar, jun], min_profit=0.02)
    # gross = 0.05, net = 0.05 - 2*0.02 = 0.01 < min_profit=0.02
    assert violations == []


def test_results_sorted_by_profit_descending():
    markets = [
        _market("tok1", yes_price=0.70, expiry_key="mar2026"),
        _market("tok2", yes_price=0.40, expiry_key="jun2026"),
        _market("tok3", yes_price=0.80, expiry_key="jan2026"),
    ]
    violations = find_cross_temporal_violations(markets, min_profit=0.01)
    profits = [v.profit for v in violations]
    assert profits == sorted(profits, reverse=True)
