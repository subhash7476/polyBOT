import pytest
from engine.bayesian import BayesianEngine, Signal
from engine.signal_filter import passes_signal_filter


def make_engine(*signals: tuple) -> BayesianEngine:
    """signals: list of (name, strength, confidence) tuples; confidence is optional."""
    eng = BayesianEngine(prior=0.5)
    for item in signals:
        name, strength = item[0], item[1]
        confidence = item[2] if len(item) > 2 else 1.0
        eng.add_signal(Signal(name=name, strength=strength, weight=0.2, confidence=confidence))
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


# ---------------------------------------------------------------------------
# Microstructure-only exception (FIX 4)
# ---------------------------------------------------------------------------

def test_micro_two_agreeing_signals_pass():
    """2 microstructure signals (flatline + OBI) with same direction → pass."""
    eng = make_engine(("flatline", 0.8), ("orderbook_imbalance", 0.6))
    ok, msg = passes_signal_filter(eng)
    assert ok is True
    assert "microstructure" in msg


def test_micro_one_high_confidence_flatline_passes():
    """1 flatline signal with confidence ≥ 0.70 → pass."""
    eng = make_engine(("flatline", 0.8, 0.75))
    ok, msg = passes_signal_filter(eng)
    assert ok is True
    assert "flatline" in msg


def test_micro_one_low_confidence_flatline_fails():
    """1 flatline signal with confidence < 0.70 → fail."""
    eng = make_engine(("flatline", 0.8, 0.50))
    ok, msg = passes_signal_filter(eng)
    assert ok is False
    assert "insufficient" in msg


def test_micro_one_obi_signal_fails():
    """1 orderbook_imbalance signal (not flatline) → fail regardless of confidence."""
    eng = make_engine(("orderbook_imbalance", 0.8, 0.90))
    ok, msg = passes_signal_filter(eng)
    assert ok is False
    assert "insufficient" in msg


def test_micro_mixed_with_non_micro_uses_old_logic():
    """Mix of microstructure + non-microstructure signals → regular logic applies."""
    # flatline (micro) + dvol (non-micro) both positive → 2 signals, 100% agreement → pass
    eng = make_engine(("flatline", 0.8), ("dvol", 0.6))
    ok, msg = passes_signal_filter(eng)
    assert ok is True
    # message should NOT say "microstructure" (it went through normal path)
    assert "microstructure" not in msg


def test_micro_two_disagreeing_signals_fail():
    """2 microstructure signals pointing opposite directions → fail."""
    eng = make_engine(("flatline", 0.8), ("orderbook_imbalance", -0.8))
    ok, msg = passes_signal_filter(eng)
    assert ok is False
    assert "microstructure signals split" in msg
