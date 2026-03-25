"""Test that new signals integrate with BayesianEngine without breaking the filter."""
import time
from datetime import datetime, timezone, timedelta
from engine.bayesian import BayesianEngine, Signal
from engine.flatline import compute_flatline_signal, record_price, _price_history
from engine.orderbook_imbalance import compute_obi_signal, _obi_history
from engine.volume_divergence import compute_vpd_signal, record_volume, _volume_history, _price_at_volume_spike
from engine.contract_parser import ParsedContract
from market.state import ContractState


def setup_function():
    _price_history.clear()
    _obi_history.clear()
    _volume_history.clear()
    _price_at_volume_spike.clear()


def _flatline_contract():
    return ParsedContract(
        token_id="tok1", question="Will BTC > $100k?",
        asset="BTC", direction="above", target_price=100000,
        expiry=datetime.now(timezone.utc) + timedelta(hours=24),
        category="crypto"
    )


def test_flatline_signal_adds_to_engine():
    engine = BayesianEngine(prior=0.55)
    engine.add_signal(Signal("vol_skew", 0.5, 0.15))

    cs = ContractState(
        yes_token_id="tok1", no_token_id="tok2",
        question="Will BTC > $100k?", category="crypto",
        best_bid=0.72, best_ask=0.76
    )
    parsed = _flatline_contract()

    now = time.time()
    for i in range(50):
        _price_history["tok1"].append((now - 3600 * (50 - i), 0.74))

    sig = compute_flatline_signal("tok1", cs, parsed)
    if sig:
        before_count = engine.signal_count
        engine.add_signal(sig)
        assert engine.signal_count == before_count + 1

    assert 0 < engine.probability < 1


def test_engine_probability_stays_bounded_with_many_signals():
    engine = BayesianEngine(prior=0.5)
    for _ in range(10):
        engine.add_signal(Signal("test", 1.0, 0.30))
    assert 0 < engine.probability < 1


def test_obi_signal_negative_strength():
    from collections import deque
    engine = BayesianEngine(prior=0.50)
    now = time.time()
    _obi_history["tok1"] = deque(maxlen=20)
    for i in range(4):
        _obi_history["tok1"].append((now - 1000 * i, 0.30))  # ask-heavy
    sig = compute_obi_signal("tok1")
    if sig:
        engine.add_signal(sig)
        assert engine.probability < 0.5  # bearish shift


def test_vpd_integrates_when_spike_detected():
    from collections import deque
    now = time.time()
    _volume_history["tok1"] = deque(maxlen=100)
    for i in range(25):
        _volume_history["tok1"].append((now - 3600 * i, 10000.0))
    _price_at_volume_spike["tok1"] = 0.55

    cs = ContractState(
        yes_token_id="tok1", no_token_id="tok2",
        question="Will BTC > $100k?", category="crypto",
        best_bid=0.55, best_ask=0.57, volume_usd=30000
    )
    sig = compute_vpd_signal("tok1", cs)
    engine = BayesianEngine(prior=0.5)
    if sig:
        engine.add_signal(sig)
        assert 0 < engine.probability < 1
