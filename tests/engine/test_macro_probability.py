import pytest
from datetime import datetime, timezone, timedelta
from engine.macro_probability import build_macro_probability, FORECAST_ERROR_STD
from engine.contract_parser import ParsedContract
from market.state import FeedState


def _make_macro_contract(asset="CPI", target=3.5, direction="above") -> ParsedContract:
    return ParsedContract(
        token_id="test",
        question=f"Will {asset} be {direction} {target}%?",
        asset=asset,
        direction=direction,
        target_price=float(target),
        expiry=datetime.now(timezone.utc) + timedelta(days=14),
        category="macro",
        parseable=True,
    )


def test_cpi_above_consensus_is_unlikely():
    """CPI target well above consensus → low probability."""
    fs = FeedState()
    fs.consensus_cpi = 3.0  # consensus
    contract = _make_macro_contract(asset="CPI", target=4.5, direction="above")
    prob, count, engine = build_macro_probability(contract, fs, {})
    assert prob < 0.3  # target is 10 std devs above consensus


def test_cpi_below_consensus_is_likely():
    """CPI target below consensus → high probability of being above it → NO reversal needed."""
    fs = FeedState()
    fs.consensus_cpi = 3.5
    contract = _make_macro_contract(asset="CPI", target=3.0, direction="above")
    prob, count, engine = build_macro_probability(contract, fs, {})
    assert prob > 0.7


def test_macro_adds_consensus_signal():
    fs = FeedState()
    fs.consensus_cpi = 3.2
    contract = _make_macro_contract(asset="CPI", target=3.5, direction="above")
    prob, count, engine = build_macro_probability(contract, fs, {"macro_consensus": 0.20})
    assert count >= 1  # consensus signal fires


def test_missing_consensus_returns_neutral():
    fs = FeedState()  # no consensus fields
    contract = _make_macro_contract(asset="CPI", target=3.5, direction="above")
    prob, count, engine = build_macro_probability(contract, fs, {})
    assert prob == 0.5
    assert count == 0


def test_unemployment_above():
    fs = FeedState()
    fs.consensus_unemployment = 4.0
    contract = _make_macro_contract(asset="UNEMPLOYMENT", target=4.5, direction="above")
    prob, count, _ = build_macro_probability(contract, fs, {})
    assert prob < 0.5  # 4.5% > consensus 4.0% → unlikely to be above


def test_forecast_error_std_keys():
    for key in ["CPI", "UNEMPLOYMENT", "NFP", "GDP"]:
        assert key in FORECAST_ERROR_STD
        assert FORECAST_ERROR_STD[key] > 0


def test_prob_clamped():
    """Probability is always in [0.01, 0.99]."""
    fs = FeedState()
    fs.consensus_cpi = 3.0
    contract_extreme_above = _make_macro_contract(asset="CPI", target=100.0, direction="above")
    prob, _, _ = build_macro_probability(contract_extreme_above, fs, {})
    assert 0.01 <= prob <= 0.99

    contract_extreme_below = _make_macro_contract(asset="CPI", target=100.0, direction="below")
    prob2, _, _ = build_macro_probability(contract_extreme_below, fs, {})
    assert 0.01 <= prob2 <= 0.99
