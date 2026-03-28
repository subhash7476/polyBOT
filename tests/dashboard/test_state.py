"""Tests for dashboard/state.py — DashboardState thread-safe snapshot store."""
import json
import time
import threading
from collections import deque

import pytest

from dashboard.state import DashboardState


# ---------------------------------------------------------------------------
# Construction / defaults
# ---------------------------------------------------------------------------

def test_initial_bot_status_is_running():
    ds = DashboardState()
    assert ds.bot_status == "running"


def test_initial_spot_prices_empty():
    ds = DashboardState()
    assert ds.spot_prices == {}


def test_initial_dvol_empty():
    ds = DashboardState()
    assert ds.dvol == {}


def test_initial_funding_rates_empty():
    ds = DashboardState()
    assert ds.funding_rates == {}


def test_initial_positions_empty():
    ds = DashboardState()
    assert list(ds.active_markets) == []
    assert ds.position_counts["open"] == 0
    assert ds.realized_pnl == 0.0


def test_initial_scan_stats_has_zero_keys():
    ds = DashboardState()
    for key in ("n_total", "n_parseable", "n_signal", "n_liquidity", "n_ev", "n_traded", "lifetime_trades"):
        assert ds.scan_stats.get(key, 0) == 0


def test_initial_feed_updated_at_is_dict():
    ds = DashboardState()
    assert isinstance(ds.feed_updated_at, dict)


# ---------------------------------------------------------------------------
# update() — snapshot writes
# ---------------------------------------------------------------------------

def test_update_spot_prices():
    ds = DashboardState()
    ds.update({"spot_prices": {"BTC": 85000.0, "ETH": 3200.0}})
    assert ds.spot_prices["BTC"] == 85000.0
    assert ds.spot_prices["ETH"] == 3200.0


def test_update_dvol():
    ds = DashboardState()
    ds.update({"dvol": {"BTC": 72.0}})
    assert ds.dvol["BTC"] == 72.0


def test_update_macro_fields():
    ds = DashboardState()
    ds.update({"dxy": 104.5, "fed_may_cut_prob": 0.35, "sofr": 3.65, "cpi": 2.7, "unrate": 4.4})
    assert ds.dxy == 104.5
    assert ds.fed_may_cut_prob == 0.35
    assert ds.sofr == 3.65
    assert abs(ds.cpi - 2.7) < 1e-9
    assert ds.unrate == 4.4


def test_update_positions_list():
    ds = DashboardState()
    pos = {
        "token_id": "tok1",
        "question": "BTC above $90k?",
        "side": "BUY_YES",
        "size_usdc": 50.0,
        "entry_price": 0.30,
        "current_mid": 0.35,
        "pnl_usdc": 8.33,
        "opened_at": "2026-03-19T10:00:00",
    }
    ds.update({"positions": [pos]})
    assert len(ds.positions) == 1
    assert ds.positions[0]["side"] == "BUY_YES"


def test_update_position_counts_and_realized_pnl():
    ds = DashboardState()
    ds.update({
        "position_counts": {"open": 2, "resolved_pending_redeem": 1, "closed": 3},
        "realized_pnl": 12.5,
        "consecutive_losses": 2,
    })
    assert ds.position_counts["open"] == 2
    assert ds.position_counts["resolved_pending_redeem"] == 1
    assert ds.realized_pnl == 12.5
    assert ds.consecutive_losses == 2


def test_update_active_markets_appends_to_deque():
    ds = DashboardState()
    for i in range(3):
        ds.update({"active_markets_append": {"question": f"q{i}", "ev": 0.05}})
    assert len(ds.active_markets) == 3
    assert ds.active_markets[-1]["question"] == "q2"


def test_active_markets_deque_maxlen_50():
    ds = DashboardState()
    for i in range(60):
        ds.update({"active_markets_append": {"question": f"q{i}"}})
    assert len(ds.active_markets) == 50
    # Oldest entries dropped
    assert ds.active_markets[0]["question"] == "q10"


def test_update_scan_stats():
    ds = DashboardState()
    ds.update({"scan_stats": {"n_total": 24, "n_parseable": 20, "n_signal": 4, "n_liquidity": 4, "n_ev": 2, "n_traded": 1, "lifetime_trades": 7}})
    assert ds.scan_stats["n_total"] == 24
    assert ds.scan_stats["n_traded"] == 1
    assert ds.scan_stats["lifetime_trades"] == 7


def test_update_feed_timestamp():
    ds = DashboardState()
    before = time.time()
    ds.update({"feed_updated_at": {"binance": time.time()}})
    assert "binance" in ds.feed_updated_at
    assert ds.feed_updated_at["binance"] >= before


def test_update_uptime_seconds():
    ds = DashboardState()
    ds.update({"uptime_seconds": 3600})
    assert ds.uptime_seconds == 3600


def test_update_unknown_key_ignored():
    ds = DashboardState()
    ds.update({"nonexistent_field": "ignored"})
    # No exception raised


# ---------------------------------------------------------------------------
# to_json() — serialisation
# ---------------------------------------------------------------------------

def test_to_json_returns_string():
    ds = DashboardState()
    result = ds.to_json()
    assert isinstance(result, str)


def test_to_json_is_valid_json():
    ds = DashboardState()
    ds.update({"spot_prices": {"BTC": 85000.0}, "dvol": {"BTC": 72.0}})
    parsed = json.loads(ds.to_json())
    assert parsed["spot_prices"]["BTC"] == 85000.0


def test_to_json_active_markets_is_list():
    ds = DashboardState()
    ds.update({"active_markets_append": {"question": "q1"}})
    parsed = json.loads(ds.to_json())
    assert isinstance(parsed["active_markets"], list)


def test_to_json_includes_lifecycle_fields():
    ds = DashboardState()
    ds.update({
        "position_counts": {"open": 1, "resolved_pending_redeem": 2, "closed": 3},
        "realized_pnl": 5.5,
        "consecutive_losses": 1,
    })
    parsed = json.loads(ds.to_json())
    assert parsed["position_counts"]["resolved_pending_redeem"] == 2
    assert parsed["realized_pnl"] == 5.5
    assert parsed["consecutive_losses"] == 1


def test_to_json_includes_lifetime_trades():
    ds = DashboardState()
    ds.update({"scan_stats": {"lifetime_trades": 5}})
    parsed = json.loads(ds.to_json())
    assert parsed["lifetime_trades"] == 5


def test_to_json_feed_ages_are_strings():
    ds = DashboardState()
    ds.update({"feed_updated_at": {"binance": time.time() - 30}})
    parsed = json.loads(ds.to_json())
    assert "30" in parsed["feed_ages"]["binance"] or "s" in parsed["feed_ages"]["binance"]


def test_to_json_unknown_feed_age_shows_never():
    ds = DashboardState()
    parsed = json.loads(ds.to_json())
    # If no timestamp recorded, feed_ages should show "never" or be absent
    assert "binance" not in parsed.get("feed_ages", {}) or parsed["feed_ages"].get("binance") == "never"


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------

def test_concurrent_updates_do_not_corrupt_state():
    ds = DashboardState()
    errors = []

    def writer(asset, price):
        try:
            for _ in range(100):
                ds.update({"spot_prices": {asset: price}})
        except Exception as e:
            errors.append(e)

    threads = [
        threading.Thread(target=writer, args=("BTC", 85000.0)),
        threading.Thread(target=writer, args=("ETH", 3200.0)),
        threading.Thread(target=writer, args=("SOL", 140.0)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    # State is readable after concurrent writes
    result = json.loads(ds.to_json())
    assert isinstance(result["spot_prices"], dict)


def test_concurrent_read_write_no_deadlock():
    ds = DashboardState()
    done = threading.Event()
    errors = []

    def reader():
        while not done.is_set():
            try:
                ds.to_json()
            except Exception as e:
                errors.append(e)

    def writer():
        for i in range(200):
            ds.update({"uptime_seconds": i})
        done.set()

    t_read = threading.Thread(target=reader)
    t_write = threading.Thread(target=writer)
    t_read.start()
    t_write.start()
    t_write.join(timeout=5)
    done.set()
    t_read.join(timeout=5)

    assert errors == []
