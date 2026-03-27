import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock
from market.state import FeedState
from engine.contract_parser import ParsedContract
from engine.probability import (
    days_to_expiry, lognormal_prob_above,
    build_model_probability,
    build_microstructure_probability,
)


def make_feeds(btc_dvol=None, btc_price=None, btc_funding_rate=None,
               dxy=None, dxy_confidence=0.0, dxy_trend=None,
               fed_may_cut_prob=None, fed_confidence=0.0) -> FeedState:
    fs = FeedState(dxy=dxy, dxy_confidence=dxy_confidence, dxy_trend=dxy_trend,
                   fed_may_cut_prob=fed_may_cut_prob, fed_confidence=fed_confidence)
    if btc_dvol is not None:
        fs.dvol["BTC"] = btc_dvol
    if btc_price is not None:
        fs.spot_prices["BTC"] = btc_price
    if btc_funding_rate is not None:
        fs.funding_rates["BTC"] = btc_funding_rate
    return fs


def make_contract(asset="BTC", direction="above", target=90000.0,
                  expiry_days=30, category="crypto") -> ParsedContract:
    expiry = datetime.now(timezone.utc) + timedelta(days=expiry_days)
    return ParsedContract(
        token_id="t1", question="test",
        asset=asset, direction=direction,
        target_price=target, expiry=expiry,
        category=category,
    )


# --- days_to_expiry ---

def test_days_to_expiry_future():
    dt = datetime.now(timezone.utc) + timedelta(days=30)
    assert abs(days_to_expiry(dt) - 30) < 0.02


def test_days_to_expiry_minimum_is_one_hour():
    dt = datetime.now(timezone.utc) - timedelta(days=1)  # past
    assert days_to_expiry(dt) == pytest.approx(1 / 24, abs=1e-6)


def test_days_to_expiry_one_hour_from_now():
    dt = datetime.now(timezone.utc) + timedelta(hours=1)
    assert days_to_expiry(dt) >= 1 / 24


# --- lognormal_prob_above ---

def test_lognormal_at_current_price_is_half():
    prob = lognormal_prob_above(spot=84000, target=84000, sigma_annual=0.80, T_days=30)
    assert abs(prob - 0.5) < 0.01


def test_lognormal_target_above_spot_less_than_half():
    prob = lognormal_prob_above(spot=84000, target=90000, sigma_annual=0.60, T_days=30)
    assert prob < 0.5


def test_lognormal_target_below_spot_greater_than_half():
    prob = lognormal_prob_above(spot=84000, target=70000, sigma_annual=0.60, T_days=30)
    assert prob > 0.5


def test_lognormal_longer_tte_widens_distribution():
    prob_30 = lognormal_prob_above(84000, 90000, 0.60, T_days=30)
    prob_90 = lognormal_prob_above(84000, 90000, 0.60, T_days=90)
    # Further expiry → more uncertainty → prob closer to 0.5
    assert prob_90 > prob_30


def test_lognormal_invalid_inputs_return_half():
    assert lognormal_prob_above(0, 90000, 0.60, 30) == 0.5
    assert lognormal_prob_above(84000, 0, 0.60, 30) == 0.5
    assert lognormal_prob_above(84000, 90000, 0.0, 30) == 0.5


# --- build_model_probability ---

def test_returns_tuple_of_three():
    feeds = make_feeds(btc_dvol=60.0, btc_price=84000.0)
    contract = make_contract()
    result = build_model_probability(contract, feeds, {"dvol_lognormal": 0.30})
    assert isinstance(result, tuple) and len(result) == 3


def test_returns_engine_as_third_element():
    from engine.bayesian import BayesianEngine
    feeds = make_feeds(btc_dvol=60.0, btc_price=84000.0)
    contract = make_contract()
    _, _, engine = build_model_probability(contract, feeds, {"dvol_lognormal": 0.30})
    assert isinstance(engine, BayesianEngine)


def test_no_feeds_returns_prior_and_zero_signals():
    feeds = FeedState()  # all empty
    contract = make_contract()
    prob, count, _ = build_model_probability(contract, feeds, {})
    assert abs(prob - 0.5) < 0.01
    assert count == 0


def test_missing_expiry_returns_prior():
    feeds = make_feeds(btc_dvol=60.0, btc_price=84000.0)
    contract = ParsedContract(token_id="t", question="q",
                               asset="BTC", direction="above",
                               target_price=90000.0, expiry=None)
    prob, count, _ = build_model_probability(contract, feeds, {})
    assert prob == 0.5
    assert count == 0


def test_missing_target_returns_prior():
    feeds = make_feeds(btc_dvol=60.0, btc_price=84000.0)
    contract = make_contract()
    contract.target_price = None
    prob, count, _ = build_model_probability(contract, feeds, {})
    assert prob == 0.5
    assert count == 0


def test_dvol_lognormal_sets_prior_not_signal():
    """dvol_lognormal is now the Bayesian prior, not a signal — count stays 0
    but the prior is no longer flat 0.5."""
    feeds = make_feeds(btc_dvol=60.0, btc_price=84000.0)
    contract = make_contract(target=90000.0, expiry_days=30)
    prob, count, engine = build_model_probability(
        contract, feeds, {"dvol_lognormal": 0.30}
    )
    assert count == 0
    assert prob != 0.5
    assert 0 < prob < 1


def test_funding_signal_fires_with_funding_rate():
    feeds = make_feeds(btc_dvol=60.0, btc_price=84000.0, btc_funding_rate=0.0005)
    contract = make_contract()
    _, count, _ = build_model_probability(
        contract, feeds, {"dvol_lognormal": 0.30, "funding_rate": 0.15}
    )
    # dvol_lognormal is the prior; funding_rate is 1 signal
    assert count >= 1


def test_macro_signal_uses_dxy_confidence():
    feeds = make_feeds(btc_dvol=60.0, btc_price=84000.0,
                       dxy=105.0, dxy_confidence=0.0, dxy_trend=0.02)
    feeds_full = make_feeds(btc_dvol=60.0, btc_price=84000.0,
                             dxy=105.0, dxy_confidence=1.0, dxy_trend=0.02)
    contract = make_contract()
    weights = {"dvol_lognormal": 0.30, "macro_dxy": 0.10}
    prob_no_conf, _, _ = build_model_probability(contract, feeds, weights)
    prob_full, _, _ = build_model_probability(contract, feeds_full, weights)
    assert prob_no_conf != prob_full


# --- build_microstructure_probability ---

def test_microstructure_probability_uses_market_mid_as_prior():
    """Mid=0.65 → prior=0.65, no signals added → prob≈0.65, signal_count=0."""
    from engine.bayesian import BayesianEngine
    contract = make_contract(category="election")
    contract_state = MagicMock()
    contract_state.mid = 0.65

    prob, count, engine = build_microstructure_probability(contract, contract_state, {})

    assert isinstance(engine, BayesianEngine)
    assert count == 0
    assert abs(prob - 0.65) < 0.01


def test_microstructure_probability_clips_extreme_mid():
    """mid=0.0 clips to 0.05; mid=1.0 clips to 0.95."""
    contract = make_contract(category="event")

    cs_low = MagicMock()
    cs_low.mid = 0.0
    prob_low, _, _ = build_microstructure_probability(contract, cs_low, {})
    assert abs(prob_low - 0.05) < 0.01

    cs_high = MagicMock()
    cs_high.mid = 1.0
    prob_high, _, _ = build_microstructure_probability(contract, cs_high, {})
    assert abs(prob_high - 0.95) < 0.01


def test_microstructure_probability_returns_three_tuple():
    contract = make_contract(category="unknown")
    contract_state = MagicMock()
    contract_state.mid = 0.50
    result = build_microstructure_probability(contract, contract_state, {})
    assert isinstance(result, tuple) and len(result) == 3
