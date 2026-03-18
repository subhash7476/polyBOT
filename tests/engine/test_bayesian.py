import pytest
import numpy as np
from engine.bayesian import BayesianEngine, Signal


def make_signal(name="test", strength=1.0, weight=0.5, confidence=1.0):
    return Signal(name=name, strength=strength, weight=weight, confidence=confidence)


# --- Initialisation ---

def test_prior_stored_as_probability():
    eng = BayesianEngine(prior=0.5)
    assert abs(eng.probability - 0.5) < 1e-9


def test_prior_above_half():
    eng = BayesianEngine(prior=0.7)
    assert eng.probability > 0.5


def test_prior_below_half():
    eng = BayesianEngine(prior=0.3)
    assert eng.probability < 0.5


def test_invalid_prior_zero_raises():
    with pytest.raises(AssertionError):
        BayesianEngine(prior=0.0)


def test_invalid_prior_one_raises():
    with pytest.raises(AssertionError):
        BayesianEngine(prior=1.0)


# --- Signal addition ---

def test_positive_signal_increases_probability():
    eng = BayesianEngine(prior=0.5)
    eng.add_signal(make_signal(strength=1.0, weight=0.5))
    assert eng.probability > 0.5


def test_negative_signal_decreases_probability():
    eng = BayesianEngine(prior=0.5)
    eng.add_signal(make_signal(strength=-1.0, weight=0.5))
    assert eng.probability < 0.5


def test_zero_strength_signal_no_change():
    eng = BayesianEngine(prior=0.6)
    before = eng.probability
    eng.add_signal(make_signal(strength=0.0, weight=0.5))
    assert abs(eng.probability - before) < 1e-9


def test_zero_confidence_signal_no_change():
    eng = BayesianEngine(prior=0.6)
    before = eng.probability
    eng.add_signal(make_signal(strength=1.0, weight=0.5, confidence=0.0))
    assert abs(eng.probability - before) < 1e-9


def test_multiple_positive_signals_accumulate():
    # 5 × (str=1.0, w=0.5) → log_odds=2.5 → sigmoid≈0.924
    eng = BayesianEngine(prior=0.5)
    for _ in range(5):
        eng.add_signal(make_signal(strength=1.0, weight=0.5))
    assert eng.probability > 0.9


# --- Clipping / stability ---

def test_probability_never_exceeds_one():
    eng = BayesianEngine(prior=0.99)
    for _ in range(100):
        eng.add_signal(make_signal(strength=1.0, weight=1.0))
    assert eng.probability <= 1.0


def test_probability_never_below_zero():
    eng = BayesianEngine(prior=0.01)
    for _ in range(100):
        eng.add_signal(make_signal(strength=-1.0, weight=1.0))
    assert eng.probability >= 0.0


def test_log_odds_clipped_at_10():
    eng = BayesianEngine(prior=0.5)
    # Add huge signal — internal log_odds should clip
    eng.add_signal(make_signal(strength=1.0, weight=100.0))
    # Clipped at 10 → probability ≈ sigmoid(10) ≈ 0.9999546
    assert eng.probability > 0.999


# --- Reset ---

def test_reset_restores_prior():
    eng = BayesianEngine(prior=0.4)
    eng.add_signal(make_signal(strength=1.0, weight=1.0))
    assert eng.probability != pytest.approx(0.4, abs=0.01)
    eng.reset()
    assert abs(eng.probability - 0.4) < 1e-6


def test_reset_clears_signals():
    eng = BayesianEngine(prior=0.5)
    eng.add_signal(make_signal())
    eng.reset()
    assert eng.signal_count == 0


# --- Signal tracking ---

def test_signal_count_increments():
    eng = BayesianEngine(prior=0.5)
    eng.add_signal(make_signal(name="a"))
    eng.add_signal(make_signal(name="b"))
    assert eng.signal_count == 2


def test_active_signals_preserves_names():
    eng = BayesianEngine(prior=0.5)
    eng.add_signal(make_signal(name="dvol_lognormal"))
    eng.add_signal(make_signal(name="funding_rate"))
    names = [s.name for s in eng.active_signals]
    assert "dvol_lognormal" in names
    assert "funding_rate" in names


# --- Summary ---

def test_summary_contains_required_keys():
    eng = BayesianEngine(prior=0.6)
    eng.add_signal(make_signal())
    s = eng.summary()
    assert "prior" in s
    assert "posterior" in s
    assert "log_odds" in s
    assert "signals" in s


def test_summary_prior_matches_init():
    eng = BayesianEngine(prior=0.65)
    assert abs(eng.summary()["prior"] - 0.65) < 0.001
