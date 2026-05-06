"""Tests for VPINPoller — VPIN computation, EMA, staleness, and incremental fetch."""

import time
import pytest
from maker.vpin_poller import VPINPoller, _compute_raw_vpin, _ema, VPIN_STALE_SECONDS


def test_compute_raw_vpin_size_weighted():
    """9 BUY x 10 size, 3 SELL x 5 size -> (90-15)/105 = 0.714 (3:1 ratio, 12 trades >= MIN)"""
    trades = (
        [{"side": "BUY",  "size": "10", "price": "0.52"}] * 9
        + [{"side": "SELL", "size": "5",  "price": "0.52"}] * 3
    )
    assert _compute_raw_vpin(trades) == pytest.approx((90 - 15) / 105, abs=0.001)


def test_compute_raw_vpin_below_min_trades():
    """Fewer than MIN_VPIN_TRADES -> neutral 0.5"""
    trades = [{"side": "BUY", "size": "10", "price": "0.50"}] * 5
    assert _compute_raw_vpin(trades) == 0.5


def test_compute_raw_vpin_empty():
    assert _compute_raw_vpin([]) == 0.5


def test_compute_raw_vpin_zero_volume():
    trades = [{"side": "BUY", "size": "0", "price": "0.50"}] * 15
    assert _compute_raw_vpin(trades) == 0.5


def test_ema_smoothing():
    """prev=0.5, raw=0.9 -> 0.7 * 0.5 + 0.3 * 0.9 = 0.62"""
    assert _ema(prev=0.5, raw=0.9) == pytest.approx(0.62, abs=0.001)


def test_vpin_tick_rule_buy_dominant():
    """Consistently rising prices -> high BUY imbalance."""
    trades = [{"price": str(0.50 + i * 0.01), "size": "10"} for i in range(15)]
    raw = _compute_raw_vpin(trades)
    assert raw > 0.5


def test_staleness_resets_to_neutral():
    """get_vpin() returns 0.5 when last update is older than VPIN_STALE_SECONDS."""
    poller = VPINPoller.__new__(VPINPoller)
    poller._vpin_cache = {"tok-a": (0.8, time.time() - (VPIN_STALE_SECONDS + 10))}
    assert poller.get_vpin("tok-a") == 0.5


def test_incremental_trades_skip_seen_ids():
    """Trades with IDs already seen are not re-processed.
    CLOB returns newest-first, so id-6 (newest) comes before id-5 (last seen)."""
    poller = VPINPoller.__new__(VPINPoller)
    poller._last_trade_id = {"tok-a": "id-5"}
    trades = [
        {"id": "id-6", "side": "BUY", "size": "10", "price": "0.5"},  # newest
        {"id": "id-5", "side": "BUY", "size": "10", "price": "0.5"},  # last seen — stop here
        {"id": "id-3", "side": "BUY", "size": "10", "price": "0.5"},  # old
    ]
    new_trades = poller._filter_new_trades("tok-a", trades)
    assert len(new_trades) == 1
    assert new_trades[0]["id"] == "id-6"
