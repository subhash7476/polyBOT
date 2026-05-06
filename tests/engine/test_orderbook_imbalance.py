import time
import pytest
from collections import deque
from engine.orderbook_imbalance import (
    record_obi_reading,
    compute_obi_signal,
    _obi_history,
)
from market.state import ContractState


def _make_cs(bid_depth=1000.0, ask_depth=400.0) -> ContractState:
    cs = ContractState(
        yes_token_id="tok1", no_token_id="tok2",
        question="Will BTC > $100k?", category="crypto",
    )
    cs.bid_depth = bid_depth
    cs.ask_depth = ask_depth
    return cs


def setup_function():
    _obi_history.clear()


def test_no_signal_when_insufficient_readings():
    cs = _make_cs(bid_depth=2000, ask_depth=500)
    record_obi_reading("tok1", cs)
    record_obi_reading("tok1", cs)  # only 2 readings
    sig = compute_obi_signal("tok1")
    assert sig is None


def test_no_signal_when_ratio_neutral():
    now = time.time()
    _obi_history["tok1"] = deque(maxlen=20)
    for i in range(5):
        _obi_history["tok1"].append((now - 1000 * i, 1.11))  # ratio ~1.11, below 2.5
    sig = compute_obi_signal("tok1")
    assert sig is None


def test_signal_fires_on_bid_heavy_book():
    now = time.time()
    _obi_history["tok1"] = deque(maxlen=20)
    for i in range(4):
        _obi_history["tok1"].append((now - 1000 * i, 3.0))  # bid/ask = 3.0 > 2.5
    sig = compute_obi_signal("tok1")
    assert sig is not None
    assert sig.name == "orderbook_imbalance"
    assert sig.strength > 0  # bid-heavy = bullish


def test_signal_fires_on_ask_heavy_book():
    now = time.time()
    _obi_history["tok1"] = deque(maxlen=20)
    for i in range(4):
        _obi_history["tok1"].append((now - 1000 * i, 0.30))  # ratio 0.30 < 0.40
    sig = compute_obi_signal("tok1")
    assert sig is not None
    assert sig.strength < 0  # ask-heavy = bearish


def test_no_signal_when_readings_not_sustained():
    now = time.time()
    _obi_history["tok1"] = deque(maxlen=20)
    ratios = [3.0, 0.3, 3.0, 0.3]
    for i, r in enumerate(ratios):
        _obi_history["tok1"].append((now - 1000 * i, r))
    sig = compute_obi_signal("tok1")
    assert sig is None


def test_record_obi_clamps_history():
    cs = _make_cs(bid_depth=1000, ask_depth=500)
    for _ in range(50):
        record_obi_reading("tok1", cs)
    assert len(_obi_history["tok1"]) <= 20


def test_no_signal_when_ask_depth_zero():
    cs = _make_cs(bid_depth=1000, ask_depth=0)
    record_obi_reading("tok1", cs)
    # Should not crash, should not record
    assert "tok1" not in _obi_history or len(_obi_history["tok1"]) == 0
