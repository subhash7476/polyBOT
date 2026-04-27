# tests/maker/test_regime.py
import pytest
from maker.regime import RegimeDecision, compute_regime_score, CATEGORY_SPREAD_MULTIPLIER


def _neutral() -> RegimeDecision:
    return compute_regime_score(
        vpin=0.5, markout_30s=0.0, inv_signed=0.0,
        hours_to_resolution=48.0, category="crypto"
    )


def test_neutral_inputs_produce_minimal_regime():
    d = _neutral()
    assert d.score < 0.05
    assert d.spread_multiplier == pytest.approx(1.0, abs=0.05)
    assert d.size_multiplier == pytest.approx(1.0, abs=0.05)
    assert d.skew_adjustment == pytest.approx(0.0, abs=0.001)
    assert d.flags == frozenset()


def test_high_vpin_sets_toxic_flow_flag():
    d = compute_regime_score(vpin=0.85, markout_30s=0.0, inv_signed=0.0,
                             hours_to_resolution=48.0, category="crypto")
    assert "toxic_flow" in d.flags


def test_bad_markout_sets_defensive_flag():
    # vpin=0.85 (vpin_score=1.0, w=0.35) + markout=-0.06 (markout_score=1.0, w=0.25) -> score=0.60
    d = compute_regime_score(vpin=0.85, markout_30s=-0.06, inv_signed=0.0,
                             hours_to_resolution=48.0, category="crypto")
    assert "defensive" in d.flags
    assert d.spread_multiplier > 1.0


def test_unwind_flag_triggers_on_high_inventory_and_short_time():
    d = compute_regime_score(vpin=0.5, markout_30s=0.0, inv_signed=0.7,
                             hours_to_resolution=2.0, category="crypto")
    assert "unwind" in d.flags


def test_unwind_flag_triggers_on_high_inventory_and_bad_markout():
    d = compute_regime_score(vpin=0.5, markout_30s=-0.02, inv_signed=0.7,
                             hours_to_resolution=24.0, category="crypto")
    assert "unwind" in d.flags


def test_extreme_flag_at_high_score():
    d = compute_regime_score(vpin=0.95, markout_30s=-0.10, inv_signed=1.0,
                             hours_to_resolution=1.0, category="crypto")
    assert "extreme" in d.flags


def test_spread_multiplier_bounded():
    import random
    random.seed(42)
    for _ in range(1000):
        d = compute_regime_score(
            vpin=random.random(),
            markout_30s=random.uniform(-0.10, 0.05),
            inv_signed=random.uniform(-1.0, 1.0),
            hours_to_resolution=random.uniform(0, 168),
            category="crypto",
        )
        assert 1.0 <= d.spread_multiplier <= 2.5, f"out of bounds: {d.spread_multiplier}"
        assert 0.25 <= d.size_multiplier <= 1.0, f"out of bounds: {d.size_multiplier}"


def test_skew_opposes_inventory_direction():
    d_long = compute_regime_score(vpin=0.5, markout_30s=0.0, inv_signed=0.8,
                                  hours_to_resolution=48.0, category="crypto")
    d_short = compute_regime_score(vpin=0.5, markout_30s=0.0, inv_signed=-0.8,
                                   hours_to_resolution=48.0, category="crypto")
    assert d_long.skew_adjustment < 0    # long -> shade fv down
    assert d_short.skew_adjustment > 0   # short -> shade fv up


def test_skew_zero_at_zero_inventory():
    d = compute_regime_score(vpin=0.5, markout_30s=0.0, inv_signed=0.0,
                             hours_to_resolution=48.0, category="crypto")
    assert d.skew_adjustment == 0.0


def test_category_spread_multiplier_table():
    assert CATEGORY_SPREAD_MULTIPLIER["finance"] < CATEGORY_SPREAD_MULTIPLIER["weather"]
    assert CATEGORY_SPREAD_MULTIPLIER["weather"] == 2.0
    assert "default" in CATEGORY_SPREAD_MULTIPLIER
