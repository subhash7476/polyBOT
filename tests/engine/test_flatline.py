import time
import pytest
from datetime import datetime, timezone, timedelta
from engine.flatline import compute_flatline_signal, record_price, _price_history
from engine.contract_parser import ParsedContract
from market.state import ContractState


def _make_contract(hours_to_expiry=48.0, direction="above") -> ParsedContract:
    expiry = datetime.now(timezone.utc) + timedelta(hours=hours_to_expiry)
    return ParsedContract(
        token_id="tok1", question="Will BTC > $100k?",
        asset="BTC", direction=direction,
        target_price=100000, expiry=expiry, category="crypto"
    )


def _make_cs(bid=0.72, ask=0.76) -> ContractState:
    return ContractState(
        yes_token_id="tok1", no_token_id="tok2",
        question="Will BTC > $100k?", category="crypto",
        best_bid=bid, best_ask=ask
    )


def setup_function():
    _price_history.clear()


def test_no_signal_when_insufficient_history():
    parsed = _make_contract(hours_to_expiry=24.0)
    cs = _make_cs()
    record_price("tok1", 0.74)
    sig = compute_flatline_signal("tok1", cs, parsed)
    assert sig is None


def test_no_signal_when_too_far_from_expiry():
    parsed = _make_contract(hours_to_expiry=100.0)
    cs = _make_cs()
    now = time.time()
    for i in range(20):
        _price_history["tok1"].append((now - 3600 * i, 0.74))
    sig = compute_flatline_signal("tok1", cs, parsed)
    assert sig is None  # 100h to expiry > 72h threshold


def test_no_signal_when_price_moves():
    parsed = _make_contract(hours_to_expiry=24.0)
    cs = _make_cs()
    now = time.time()
    prices = [0.65, 0.70, 0.68, 0.72, 0.65, 0.70]
    for i, p in enumerate(prices):
        _price_history["tok1"].append((now - 3600 * (len(prices) - i), p))
    sig = compute_flatline_signal("tok1", cs, parsed)
    assert sig is None


def test_signal_fires_on_flatline():
    parsed = _make_contract(hours_to_expiry=24.0)
    cs = _make_cs(bid=0.72, ask=0.76)
    now = time.time()
    for i in range(50):
        _price_history["tok1"].append((now - 3600 * (50 - i), 0.74 + (i % 2) * 0.005))
    sig = compute_flatline_signal("tok1", cs, parsed)
    assert sig is not None
    assert sig.name == "flatline"
    assert sig.strength > 0
    assert 0 < sig.weight <= 0.30


def test_signal_below_direction():
    parsed = _make_contract(hours_to_expiry=24.0, direction="below")
    cs = _make_cs(bid=0.72, ask=0.76)
    now = time.time()
    for i in range(50):
        _price_history["tok1"].append((now - 3600 * (50 - i), 0.74))
    sig = compute_flatline_signal("tok1", cs, parsed)
    assert sig is not None
    assert sig.strength > 0


def test_no_signal_when_leading_price_too_low():
    parsed = _make_contract(hours_to_expiry=24.0)
    cs = _make_cs(bid=0.52, ask=0.56)
    now = time.time()
    for i in range(50):
        _price_history["tok1"].append((now - 3600 * (50 - i), 0.54))
    sig = compute_flatline_signal("tok1", cs, parsed)
    assert sig is None


def test_record_price_limits_history():
    for i in range(300):
        record_price("tok1", 0.74)
    assert len(_price_history["tok1"]) <= 200
