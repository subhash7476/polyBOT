import time
import pytest
from collections import deque
from engine.volume_divergence import (
    record_volume,
    compute_vpd_signal,
    _volume_history,
    _price_at_volume_spike,
)
from market.state import ContractState


def _make_cs(mid=0.55, volume_usd=50000.0) -> ContractState:
    cs = ContractState(
        yes_token_id="tok1", no_token_id="tok2",
        question="Will BTC > $100k?", category="crypto",
        best_bid=mid - 0.01, best_ask=mid + 0.01, volume_usd=volume_usd
    )
    return cs


def setup_function():
    _volume_history.clear()
    _price_at_volume_spike.clear()


def test_no_signal_when_insufficient_history():
    cs = _make_cs(volume_usd=100000)
    record_volume("tok1", cs)
    sig = compute_vpd_signal("tok1", cs)
    assert sig is None


def test_no_signal_when_volume_not_elevated():
    now = time.time()
    _volume_history["tok1"] = deque(maxlen=100)
    for i in range(25):
        _volume_history["tok1"].append((now - 3600 * i, 10000.0))
    cs = _make_cs(volume_usd=15000)  # only 1.5x avg, below 2x
    sig = compute_vpd_signal("tok1", cs)
    assert sig is None


def test_no_signal_when_price_moves_with_volume():
    now = time.time()
    _volume_history["tok1"] = deque(maxlen=100)
    for i in range(25):
        _volume_history["tok1"].append((now - 3600 * i, 10000.0))
    _price_at_volume_spike["tok1"] = 0.50
    cs = _make_cs(mid=0.58, volume_usd=30000)  # 3x avg + 8% price move (> 2%)
    sig = compute_vpd_signal("tok1", cs)
    assert sig is None


def test_signal_fires_on_volume_spike_no_price_move():
    now = time.time()
    _volume_history["tok1"] = deque(maxlen=100)
    for i in range(25):
        _volume_history["tok1"].append((now - 3600 * i, 10000.0))
    _price_at_volume_spike["tok1"] = 0.55
    cs = _make_cs(mid=0.56, volume_usd=30000)  # 3x avg, only 1% price move
    sig = compute_vpd_signal("tok1", cs)
    assert sig is not None
    assert sig.name == "volume_divergence"
    assert sig.strength > 0  # price drifted up slightly -> bullish
    assert sig.weight <= 0.15


def test_signal_direction_negative_when_price_drifts_down():
    now = time.time()
    _volume_history["tok1"] = deque(maxlen=100)
    for i in range(25):
        _volume_history["tok1"].append((now - 3600 * i, 10000.0))
    _price_at_volume_spike["tok1"] = 0.56
    cs = _make_cs(mid=0.55, volume_usd=30000)  # price drifted DOWN -> bearish
    sig = compute_vpd_signal("tok1", cs)
    assert sig is not None
    assert sig.strength < 0  # bearish


def test_first_detection_records_but_no_signal():
    now = time.time()
    _volume_history["tok1"] = deque(maxlen=100)
    for i in range(25):
        _volume_history["tok1"].append((now - 3600 * i, 10000.0))
    # No entry in _price_at_volume_spike yet
    cs = _make_cs(mid=0.55, volume_usd=30000)  # 3x avg = spike
    sig = compute_vpd_signal("tok1", cs)
    assert sig is None  # first detection - records price, no signal yet
    assert "tok1" in _price_at_volume_spike  # price was recorded


def test_record_volume_limits_history():
    cs = _make_cs(volume_usd=10000)
    for _ in range(200):
        record_volume("tok1", cs)
    assert len(_volume_history["tok1"]) <= 100
