"""Tests for MarkoutTracker — markout scheduling, evaluation, and stats."""

import asyncio
import json
import time
import pytest

from maker.markout_tracker import MarkoutTracker, MARKOUT_INTERVALS
from maker.state import MakerState
from maker.types import Fill
from market.state import AppState, ContractState


def _make_app_state(token_id: str, bid: float, ask: float) -> AppState:
    app = AppState()
    cs = ContractState(
        yes_token_id=token_id,
        no_token_id="no-" + token_id,
        question="test",
        category="crypto",
        best_bid=bid,
        best_ask=ask,
    )
    app.markets[token_id] = cs
    return app


def _make_fill(token_id: str, side: str, price: float, mid_at_fill: float, filled_at: float) -> Fill:
    return Fill(
        token_id=token_id,
        side=side,
        price=price,
        size=10.0,
        order_id=f"ord-{side}-{price}",
        filled_at=filled_at,
        mid_at_fill=mid_at_fill,
    )


@pytest.mark.asyncio
async def test_markout_tracker_schedules_and_evaluates(tmp_path):
    """Fill fed into markout_q → markout evaluated after interval elapses."""
    token_id = "tok-abc"
    mid_at_fill = 0.50
    # Market moves to 0.45 after fill (price dropped — adverse for BUY)
    app = _make_app_state(token_id, bid=0.44, ask=0.46)  # mid = 0.45

    maker_state = MakerState()
    markout_q: asyncio.Queue = asyncio.Queue()
    log_path = tmp_path / "fills_markout.jsonl"
    tracker = MarkoutTracker(app, maker_state, markout_q, log_path=str(log_path))

    # Fill from 2s ago so all T+5/30/60 checks due at different times
    # We'll only test T+5 by using filled_at 5s in the past
    past = time.time() - 6  # filled 6s ago → T+5 is due, T+30/60 not yet
    fill = _make_fill(token_id, "BUY", price=0.49, mid_at_fill=mid_at_fill, filled_at=past)
    await markout_q.put(fill)

    # Run tracker for just enough time to process T+5 check
    async def _run_briefly():
        try:
            await asyncio.wait_for(tracker.run(), timeout=0.5)
        except asyncio.TimeoutError:
            pass

    await _run_briefly()

    stats = tracker.stats
    assert stats["markout_fills"] == 1
    # T+5 check should have fired: markout = +1 * (0.45 - 0.50) = -0.05 (adverse)
    assert stats["avg_markout_5s"] == pytest.approx(-0.05, abs=0.001)
    assert stats["adverse_rate_5s"] == 1.0  # 100% adverse

    # Log file should have one record for the T+5 interval
    records = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert len(records) >= 1
    rec = records[0]
    assert rec["interval_s"] == 5
    assert rec["side"] == "BUY"
    assert abs(rec["markout"] - (-0.05)) < 0.001


@pytest.mark.asyncio
async def test_markout_positive_for_sell_adverse(tmp_path):
    """SELL fill followed by mid rising → negative markout (adverse)."""
    token_id = "tok-sell"
    mid_at_fill = 0.40
    # Mid rose to 0.50 after we sold — bad for us
    app = _make_app_state(token_id, bid=0.49, ask=0.51)  # mid = 0.50

    maker_state = MakerState()
    markout_q: asyncio.Queue = asyncio.Queue()
    tracker = MarkoutTracker(app, maker_state, markout_q, log_path=str(tmp_path / "m.jsonl"))

    past = time.time() - 6
    fill = _make_fill(token_id, "SELL", price=0.41, mid_at_fill=mid_at_fill, filled_at=past)
    await markout_q.put(fill)

    async def _run():
        try:
            await asyncio.wait_for(tracker.run(), timeout=0.5)
        except asyncio.TimeoutError:
            pass

    await _run()
    stats = tracker.stats
    # markout = -1 * (0.50 - 0.40) = -0.10
    assert stats["avg_markout_5s"] == pytest.approx(-0.10, abs=0.001)
    assert stats["adverse_rate_5s"] == 1.0


@pytest.mark.asyncio
async def test_markout_positive_spread_capture(tmp_path):
    """BUY fill where mid rises afterward → positive markout (good)."""
    token_id = "tok-good"
    mid_at_fill = 0.50
    # Mid rose to 0.55 after buy
    app = _make_app_state(token_id, bid=0.54, ask=0.56)  # mid = 0.55

    maker_state = MakerState()
    markout_q: asyncio.Queue = asyncio.Queue()
    tracker = MarkoutTracker(app, maker_state, markout_q, log_path=str(tmp_path / "m.jsonl"))

    past = time.time() - 6
    fill = _make_fill(token_id, "BUY", price=0.49, mid_at_fill=mid_at_fill, filled_at=past)
    await markout_q.put(fill)

    async def _run():
        try:
            await asyncio.wait_for(tracker.run(), timeout=0.5)
        except asyncio.TimeoutError:
            pass

    await _run()
    stats = tracker.stats
    # markout = +1 * (0.55 - 0.50) = +0.05
    assert stats["avg_markout_5s"] == pytest.approx(0.05, abs=0.001)
    assert stats["adverse_rate_5s"] == 0.0


@pytest.mark.asyncio
async def test_stats_empty_before_fills():
    """Stats return zeros when no fills have been evaluated yet."""
    app = AppState()
    maker_state = MakerState()
    markout_q: asyncio.Queue = asyncio.Queue()
    tracker = MarkoutTracker(app, maker_state, markout_q, log_path="/dev/null")

    stats = tracker.stats
    assert stats["markout_fills"] == 0
    for interval in MARKOUT_INTERVALS:
        assert stats[f"avg_markout_{interval}s"] == 0.0
        assert stats[f"adverse_rate_{interval}s"] == 0.0


@pytest.mark.asyncio
async def test_missing_market_skips_silently(tmp_path):
    """If market disappears before check fires, no crash and no log record."""
    app = AppState()  # no markets registered
    maker_state = MakerState()
    markout_q: asyncio.Queue = asyncio.Queue()
    log_path = tmp_path / "m.jsonl"
    tracker = MarkoutTracker(app, maker_state, markout_q, log_path=str(log_path))

    past = time.time() - 6
    fill = _make_fill("unknown-token", "BUY", 0.50, 0.50, past)
    await markout_q.put(fill)

    async def _run():
        try:
            await asyncio.wait_for(tracker.run(), timeout=0.5)
        except asyncio.TimeoutError:
            pass

    await _run()
    # No records written, no crash
    assert not log_path.exists() or log_path.read_text().strip() == ""


from collections import deque as _deque
from maker.markout_tracker import MIN_MARKET_FILLS, MIN_CATEGORY_FILLS


def _tracker_with_market_fills(n_fills: int, category: str, token_id: str,
                                markout_val: float, tmp_path) -> "MarkoutTracker":
    """Build a MarkoutTracker and inject pre-evaluated T+30s markout samples directly."""
    app = _make_app_state(token_id, bid=0.49, ask=0.51)
    app.markets[token_id].category = category
    ms = MakerState()
    q: asyncio.Queue = asyncio.Queue()
    tracker = MarkoutTracker(app, ms, q, log_path=str(tmp_path / "m.jsonl"))

    tracker._market_markouts[token_id] = {i: _deque(maxlen=20) for i in MARKOUT_INTERVALS}
    tracker.by_category.setdefault(category, {i: _deque(maxlen=500) for i in MARKOUT_INTERVALS})
    for _ in range(n_fills):
        tracker._market_markouts[token_id][30].append(markout_val)
        tracker.by_category[category][30].append(markout_val)
        tracker._n_fills += 1
    return tracker


def test_get_markout_30s_returns_per_market_when_sufficient(tmp_path):
    tracker = _tracker_with_market_fills(25, "crypto", "tok-a", -0.01, tmp_path)
    result = tracker.get_markout_30s("tok-a", "crypto")
    assert result == pytest.approx(-0.01, abs=0.001)


def test_get_markout_30s_falls_back_to_category(tmp_path):
    # Per-market only 10 fills (below MIN_MARKET_FILLS=20) — uses category (100+ fills)
    tracker = _tracker_with_market_fills(10, "crypto", "tok-b", -0.02, tmp_path)
    for _ in range(100):
        tracker.by_category["crypto"][30].append(-0.02)
    result = tracker.get_markout_30s("tok-b", "crypto")
    assert result == pytest.approx(-0.02, abs=0.001)


def test_get_markout_30s_returns_zero_when_no_data(tmp_path):
    app = AppState()
    ms = MakerState()
    q: asyncio.Queue = asyncio.Queue()
    tracker = MarkoutTracker(app, ms, q, log_path=str(tmp_path / "m.jsonl"))
    assert tracker.get_markout_30s("unknown-token", "finance") == 0.0
