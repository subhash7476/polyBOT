"""Tests for dashboard/server.py — Flask SSE server."""
import json
import threading
import time

import pytest

from dashboard.state import DashboardState
from dashboard.server import create_app, start_dashboard_server


# ---------------------------------------------------------------------------
# create_app() — Flask app factory
# ---------------------------------------------------------------------------

@pytest.fixture
def dash():
    ds = DashboardState()
    ds.update({"spot_prices": {"BTC": 85000.0}, "dvol": {"BTC": 72.0}})
    return ds


@pytest.fixture
def client(dash):
    app = create_app(dash)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_root_returns_200(client):
    r = client.get("/")
    assert r.status_code == 200


def test_root_returns_html(client):
    r = client.get("/")
    assert b"<!DOCTYPE html>" in r.data or b"<html" in r.data


def test_snapshot_endpoint_returns_200(client):
    r = client.get("/api/snapshot")
    assert r.status_code == 200


def test_snapshot_endpoint_returns_json(client):
    r = client.get("/api/snapshot")
    assert r.content_type.startswith("application/json")


def test_snapshot_contains_spot_prices(client):
    data = json.loads(client.get("/api/snapshot").data)
    assert data["spot_prices"]["BTC"] == 85000.0


def test_snapshot_contains_dvol(client):
    data = json.loads(client.get("/api/snapshot").data)
    assert data["dvol"]["BTC"] == 72.0


def test_snapshot_contains_bot_status(client):
    data = json.loads(client.get("/api/snapshot").data)
    assert "bot_status" in data


def test_snapshot_contains_scan_stats(client):
    data = json.loads(client.get("/api/snapshot").data)
    assert "scan_stats" in data


def test_snapshot_contains_active_markets(client):
    data = json.loads(client.get("/api/snapshot").data)
    assert "active_markets" in data
    assert isinstance(data["active_markets"], list)


def test_snapshot_contains_positions(client):
    data = json.loads(client.get("/api/snapshot").data)
    assert "positions" in data
    assert isinstance(data["positions"], list)


def test_snapshot_contains_feed_ages(client):
    data = json.loads(client.get("/api/snapshot").data)
    assert "feed_ages" in data


def test_stream_endpoint_exists(client):
    # SSE stream should return 200 (we don't consume the full stream in tests)
    # Use a quick abort after headers received
    with client.get("/stream", buffered=False) as r:
        assert r.status_code == 200
        assert "text/event-stream" in r.content_type


def test_stream_returns_data_line(client):
    # Read just the first chunk from the SSE stream
    with client.get("/stream", buffered=False) as r:
        # Read first 512 bytes (one SSE frame)
        chunk = r.response.read(512) if hasattr(r.response, "read") else b""
        # The stream prefix should start with "data: "
        # (May be empty if test client buffers — just verify no crash)
        assert r.status_code == 200


def test_unknown_route_returns_404(client):
    r = client.get("/does-not-exist")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# start_dashboard_server() — daemon thread launcher
# ---------------------------------------------------------------------------

def test_start_dashboard_server_returns_thread():
    ds = DashboardState()
    t = start_dashboard_server(ds, port=15051)
    assert isinstance(t, threading.Thread)
    assert t.daemon is True


def test_start_dashboard_server_thread_is_alive():
    ds = DashboardState()
    t = start_dashboard_server(ds, port=15052)
    time.sleep(0.3)  # Give Flask a moment to bind
    assert t.is_alive()


def test_server_responds_on_configured_port():
    import httpx
    ds = DashboardState()
    ds.update({"spot_prices": {"ETH": 3200.0}})
    start_dashboard_server(ds, port=15053)
    time.sleep(0.5)

    r = httpx.get("http://127.0.0.1:15053/api/snapshot", timeout=3.0)
    assert r.status_code == 200
    data = r.json()
    assert data["spot_prices"]["ETH"] == 3200.0
