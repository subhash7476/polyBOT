import pytest
from engine.bayesian import BayesianEngine, Signal
from engine.signal_filter import passes_signal_filter


def make_engine(*signals: tuple) -> BayesianEngine:
    """signals: list of (name, strength) tuples."""
    eng = BayesianEngine(prior=0.5)
    for name, strength in signals:
        eng.add_signal(Signal(name=name, strength=strength, weight=0.2))
    return eng


def test_passes_with_two_agreeing_signals():
    eng = make_engine(("dvol", 0.8), ("funding", 0.6))
    ok, msg = passes_signal_filter(eng)
    assert ok is True


def test_fails_with_only_one_signal():
    eng = make_engine(("dvol", 0.8),)
    ok, msg = passes_signal_filter(eng)
    assert ok is False
    assert "only 1" in msg


def test_fails_with_zero_signals():
    eng = BayesianEngine(prior=0.5)
    ok, msg = passes_signal_filter(eng)
    assert ok is False


def test_fails_when_signals_split_evenly():
    # 1 positive, 1 negative → 50% agreement < 60% threshold
    eng = make_engine(("a", 0.8), ("b", -0.8))
    ok, msg = passes_signal_filter(eng)
    assert ok is False
    assert "agreement" in msg


def test_passes_with_3_of_4_agreeing():
    # 3 positive, 1 negative → 75% agreement ≥ 60%
    eng = make_engine(("a", 0.8), ("b", 0.6), ("c", 0.4), ("d", -0.5))
    ok, msg = passes_signal_filter(eng)
    assert ok is True


def test_custom_min_signals():
    eng = make_engine(("a", 0.5), ("b", 0.5), ("c", 0.5))
    ok, _ = passes_signal_filter(eng, min_signals=4)
    assert ok is False


def test_zero_strength_signals_are_neutral():
    # Zero strength signals → neither positive nor negative
    eng = make_engine(("a", 0.0), ("b", 0.0))
    # 0 positive, 0 negative → max(0,0)/2 = 0% agreement → fails
    ok, _ = passes_signal_filter(eng)
    assert ok is False
