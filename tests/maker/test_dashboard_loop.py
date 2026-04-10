"""Tests for maker/dashboard_loop.py helpers."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from maker.dashboard_loop import _days_left


# ── _days_left() ──────────────────────────────────────────────────────────────

def test_days_left_with_future_date():
    future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    result = _days_left(future)
    assert result is not None
    assert 6.9 < result < 7.1


def test_days_left_with_past_date():
    past = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    result = _days_left(past)
    assert result is not None
    assert result < 0


def test_days_left_with_z_suffix():
    future = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = _days_left(future)
    assert result is not None
    assert 1.9 < result < 2.1


def test_days_left_with_none():
    assert _days_left(None) is None


def test_days_left_with_empty_string():
    assert _days_left("") is None


def test_days_left_with_invalid():
    assert _days_left("not-a-date") is None


def test_days_left_with_garbage():
    assert _days_left("2026-99-99T00:00:00Z") is None


# ── selected_markets list construction ───────────────────────────────────────

def _make_cs(question, best_bid, best_ask, volume_24h, end_date_iso="", category="crypto"):
    """Build a minimal ContractState-like mock."""
    cs = MagicMock()
    cs.question = question
    cs.best_bid = best_bid
    cs.best_ask = best_ask
    cs.volume_24h = volume_24h
    cs.volume_usd = volume_24h  # fallback
    cs.end_date_iso = end_date_iso
    cs.category = category
    return cs


def _build_selected_markets(selected_token_ids, markets_snapshot, live_orders):
    """Replicate the selected_markets logic from dashboard_loop exactly."""
    selected_markets = []
    for token_id in selected_token_ids:
        cs = markets_snapshot.get(token_id)
        if cs is None:
            continue
        dl = _days_left(cs.end_date_iso)
        selected_markets.append({
            "token_id": token_id[:16],
            "question": cs.question[:70],
            "category": cs.category,
            "book_bid": cs.best_bid,
            "book_ask": cs.best_ask,
            "spread": round(cs.best_ask - cs.best_bid, 4),
            "volume_24h": getattr(cs, 'volume_24h', None) or cs.volume_usd,
            "days_left": dl,
            "is_quoting": token_id in live_orders,
        })
    selected_markets.sort(key=lambda m: (not m["is_quoting"], -(m["volume_24h"] or 0)))
    return selected_markets


def test_selected_markets_quoting_first():
    """Markets being actively quoted appear before non-quoted ones."""
    tid_quoting = "aaa" * 20
    tid_idle = "bbb" * 20

    markets_snapshot = {
        tid_quoting: _make_cs("Will BTC hit 100k?", 0.45, 0.55, volume_24h=500.0),
        tid_idle: _make_cs("Will ETH hit 5k?", 0.30, 0.70, volume_24h=1000.0),
    }
    live_orders = {tid_quoting: []}

    result = _build_selected_markets(
        {tid_quoting, tid_idle}, markets_snapshot, live_orders
    )

    assert len(result) == 2
    assert result[0]["is_quoting"] is True
    assert result[1]["is_quoting"] is False


def test_selected_markets_sorted_by_volume_when_not_quoting():
    """Among non-quoted markets, higher volume appears first."""
    tid_low = "ccc" * 20
    tid_high = "ddd" * 20

    markets_snapshot = {
        tid_low: _make_cs("Low vol market", 0.4, 0.6, volume_24h=100.0),
        tid_high: _make_cs("High vol market", 0.4, 0.6, volume_24h=5000.0),
    }
    live_orders = {}

    result = _build_selected_markets(
        {tid_low, tid_high}, markets_snapshot, live_orders
    )

    assert len(result) == 2
    assert result[0]["volume_24h"] == 5000.0
    assert result[1]["volume_24h"] == 100.0


def test_selected_markets_missing_token_id_skipped():
    """Token IDs not in markets_snapshot are silently skipped."""
    tid_present = "eee" * 20
    tid_missing = "fff" * 20

    markets_snapshot = {
        tid_present: _make_cs("Present market", 0.4, 0.6, volume_24h=200.0),
    }
    live_orders = {}

    result = _build_selected_markets(
        {tid_present, tid_missing}, markets_snapshot, live_orders
    )

    assert len(result) == 1
    assert result[0]["token_id"] == tid_present[:16]


def test_selected_markets_fields():
    """All required fields are present in each row."""
    tid = "ggg" * 20
    future_iso = (datetime.now(timezone.utc) + timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")

    markets_snapshot = {
        tid: _make_cs("Test question longer than 70 chars abcdefghijklmnopqrstuvwxyz1234567890!",
                      0.45, 0.55, volume_24h=300.0, end_date_iso=future_iso, category="politics"),
    }
    live_orders = {tid: []}

    result = _build_selected_markets({tid}, markets_snapshot, live_orders)

    assert len(result) == 1
    row = result[0]
    required_fields = {
        "token_id", "question", "category", "book_bid", "book_ask",
        "spread", "volume_24h", "days_left", "is_quoting",
    }
    assert required_fields.issubset(row.keys())
    assert len(row["question"]) <= 70
    assert row["category"] == "politics"
    assert row["is_quoting"] is True
    assert row["days_left"] is not None and row["days_left"] > 9.0


def test_selected_markets_spread_computed():
    """Spread is correctly computed as ask - bid."""
    tid = "hhh" * 20
    markets_snapshot = {
        tid: _make_cs("Spread test", 0.42, 0.58, volume_24h=100.0),
    }
    result = _build_selected_markets({tid}, markets_snapshot, {})
    assert result[0]["spread"] == pytest.approx(0.16, abs=1e-4)
