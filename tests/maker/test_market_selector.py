import time
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timezone, timedelta
from market.state import ContractState, FeedState, FalconMarketInsight
from maker.market_selector import MarketSelector


def _make_market(
    token_id: str,
    category: str = "sports",
    bid: float = 0.40,
    ask: float = 0.60,
    volume: float = 5_000.0,
    condition_id: str = "",
) -> ContractState:
    return ContractState(
        yes_token_id=token_id,
        no_token_id=f"no-{token_id}",
        question=f"Will team A beat team B? [{token_id}]",
        category=category,
        best_bid=bid,
        best_ask=ask,
        volume_usd=volume,
        condition_id=condition_id or token_id,
    )


def test_only_unknown_category_excluded():
    """MarketSelector is category-agnostic — only 'unknown' is excluded."""
    markets = {
        "crypto1":    _make_market("crypto1",    category="crypto"),
        "sports1":    _make_market("sports1",    category="sports"),
        "event1":     _make_market("event1",     category="event"),
        "election1":  _make_market("election1",  category="election"),
        "politics1":  _make_market("politics1",  category="politics"),
        "unknown1":   _make_market("unknown1",   category="unknown"),
    }
    selected = MarketSelector.filter_and_rank(markets)
    assert "crypto1"   in selected
    assert "sports1"   in selected
    assert "event1"    in selected
    assert "election1" in selected
    assert "politics1" in selected
    assert "unknown1"  not in selected   # only 'unknown' is excluded


def test_filters_low_volume():
    markets = {
        "thin": _make_market("thin", volume=50.0),
        "ok":   _make_market("ok",   volume=2_000.0),
    }
    selected = MarketSelector.filter_and_rank(markets)
    assert "ok" in selected
    assert "thin" not in selected


def test_filters_tight_spread():
    markets = {
        "tight": _make_market("tight", bid=0.498, ask=0.502),  # 0.4c spread — below 1c threshold
        "wide":  _make_market("wide",  bid=0.40,  ask=0.60),   # 20c spread
    }
    selected = MarketSelector.filter_and_rank(markets)
    assert "wide" in selected
    assert "tight" not in selected


def test_ranks_by_spread_times_volume():
    markets = {
        "a": _make_market("a", bid=0.40, ask=0.50, volume=1_000.0),  # score=100
        "b": _make_market("b", bid=0.40, ask=0.60, volume=2_000.0),  # score=400
    }
    selected = MarketSelector.filter_and_rank(markets)
    keys = list(selected.keys())
    assert keys[0] == "b"  # higher score first


def test_caps_at_max_active():
    markets = {
        f"m{i}": _make_market(f"m{i}", volume=float(10_000 - i))
        for i in range(30)
    }
    selected = MarketSelector.filter_and_rank(markets, max_markets=20)
    assert len(selected) == 20


# ── Falcon scoring ────────────────────────────────────────────────────────────

def _fresh_insight(condition_id: str, **kwargs) -> FalconMarketInsight:
    defaults = dict(
        condition_id=condition_id,
        question="Test market",
        volume_trend="Normal",
        top1_wallet_pct=20.0,
        whale_control_flag=False,
        unique_traders_7d=100,
        trade_concentration_flag=False,
        fetched_at=time.time(),
    )
    defaults.update(kwargs)
    return FalconMarketInsight(**defaults)


def test_falcon_no_feeds_uses_base_score():
    """Without feeds, ranking is pure spread × volume — no Falcon adjustment."""
    markets = {
        "a": _make_market("a", bid=0.40, ask=0.50, volume=1_000.0),  # base=100
        "b": _make_market("b", bid=0.40, ask=0.60, volume=2_000.0),  # base=400
    }
    selected = MarketSelector.filter_and_rank(markets, feeds=None)
    assert list(selected.keys())[0] == "b"


def test_falcon_whale_control_demotes_market():
    """A whale-controlled market (×0.3) should rank below a normal market."""
    markets = {
        "whale": _make_market("whale", bid=0.40, ask=0.60, volume=10_000.0, condition_id="cwhale"),
        "clean": _make_market("clean", bid=0.40, ask=0.60, volume=5_000.0, condition_id="cclean"),
    }
    feeds = FeedState()
    feeds.falcon_market_insights["cwhale"] = _fresh_insight("cwhale", whale_control_flag=True)
    feeds.falcon_market_insights["cclean"] = _fresh_insight("cclean")

    selected = MarketSelector.filter_and_rank(markets, feeds=feeds)
    keys = list(selected.keys())
    # whale has 2× volume but 0.3× Falcon mult → score=600; clean → score=1000
    assert keys[0] == "clean"


def test_falcon_spiking_trend_boosts_market():
    """Spiking volume trend (×1.5) should lift a market above a larger normal one."""
    markets = {
        "spike": _make_market("spike", bid=0.40, ask=0.60, volume=5_000.0, condition_id="cspike"),
        "normal": _make_market("normal", bid=0.40, ask=0.60, volume=6_000.0, condition_id="cnorm"),
    }
    feeds = FeedState()
    feeds.falcon_market_insights["cspike"] = _fresh_insight("cspike", volume_trend="Spiking")
    feeds.falcon_market_insights["cnorm"] = _fresh_insight("cnorm", volume_trend="Normal")

    selected = MarketSelector.filter_and_rank(markets, feeds=feeds)
    keys = list(selected.keys())
    # spike: 5000 × 0.20 × 1.5 = 1500; normal: 6000 × 0.20 × 1.0 = 1200
    assert keys[0] == "spike"


def test_falcon_dying_interest_strongly_demotes():
    """Dying Interest (×0.2) drops a high-volume market far down."""
    markets = {
        "dying": _make_market("dying", bid=0.40, ask=0.60, volume=20_000.0, condition_id="cdying"),
        "ok":    _make_market("ok",    bid=0.40, ask=0.60, volume=5_000.0, condition_id="cok"),
    }
    feeds = FeedState()
    feeds.falcon_market_insights["cdying"] = _fresh_insight("cdying", volume_trend="Dying Interest")
    feeds.falcon_market_insights["cok"] = _fresh_insight("cok")

    selected = MarketSelector.filter_and_rank(markets, feeds=feeds)
    keys = list(selected.keys())
    # dying: 20000 × 0.20 × 0.2 = 800; ok: 5000 × 0.20 × 1.0 = 1000
    assert keys[0] == "ok"


def test_falcon_stale_insight_ignored():
    """Stale Falcon data returns neutral (1.0×), so base score is used unchanged."""
    markets = {
        "m": _make_market("m", bid=0.40, ask=0.60, volume=5_000.0, condition_id="cm"),
    }
    feeds = FeedState()
    feeds.falcon_market_insights["cm"] = _fresh_insight(
        "cm", whale_control_flag=True, fetched_at=time.time() - 99999
    )
    selected = MarketSelector.filter_and_rank(markets, feeds=feeds)
    assert "m" in selected  # stale → not excluded, neutral mult applied


def test_falcon_spiking_bypasses_tight_spread_filter():
    """A Falcon-spiking market with tight spread should be included (bypass _MIN_SPREAD)."""
    markets = {
        "tight_spiking": _make_market(
            "tight_spiking", bid=0.498, ask=0.502,  # 0.4c spread — below 1c threshold, normally filtered
            volume=50_000.0, condition_id="cspike",
        ),
        "wide_normal": _make_market(
            "wide_normal", bid=0.40, ask=0.60, volume=5_000.0, condition_id="cnorm",
        ),
    }
    feeds = FeedState()
    feeds.falcon_market_insights["cspike"] = _fresh_insight(
        "cspike", volume_trend="Spiking", whale_control_flag=False,
    )
    feeds.falcon_market_insights["cnorm"] = _fresh_insight("cnorm", volume_trend="Normal")

    selected = MarketSelector.filter_and_rank(markets, feeds=feeds)
    assert "tight_spiking" in selected    # bypassed spread filter via Falcon Spiking
    assert "wide_normal" in selected


def test_falcon_spiking_bypass_blocked_for_whale_controlled():
    """Whale-controlled Spiking market should NOT bypass the spread filter."""
    markets = {
        "whale_tight": _make_market(
            # 0.4c spread — below the 1c _MIN_SPREAD threshold — needs bypass to pass
            "whale_tight", bid=0.498, ask=0.502, volume=50_000.0, condition_id="cwh",
        ),
    }
    feeds = FeedState()
    feeds.falcon_market_insights["cwh"] = _fresh_insight(
        "cwh", volume_trend="Spiking", whale_control_flag=True,
    )
    selected = MarketSelector.filter_and_rank(markets, feeds=feeds)
    assert "whale_tight" not in selected   # whale-controlled — no bypass


def test_falcon_diverse_flow_boosts():
    """unique_traders_7d > 1000 applies ×1.3 boost."""
    markets = {
        "diverse": _make_market("diverse", bid=0.40, ask=0.60, volume=5_000.0, condition_id="cdiv"),
        "sparse":  _make_market("sparse",  bid=0.40, ask=0.60, volume=5_000.0, condition_id="cspa"),
    }
    feeds = FeedState()
    feeds.falcon_market_insights["cdiv"] = _fresh_insight("cdiv", unique_traders_7d=2000)
    feeds.falcon_market_insights["cspa"] = _fresh_insight("cspa", unique_traders_7d=100)

    selected = MarketSelector.filter_and_rank(markets, feeds=feeds)
    keys = list(selected.keys())
    assert keys[0] == "diverse"


def test_falcon_question_fallback_bypasses_tight_spread():
    """Spiking bypass works even when condition_ids don't match (question-based fallback).

    Polymarket's Gamma conditionId and Falcon's condition_id are often different
    identifiers for the same market.  The question-based fallback ensures we still
    get the bypass for high-volume spiking markets.
    """
    # Market in state has condition_id "gamma-cid"; Falcon insight has "falcon-cid"
    # — two different IDs for the same market, matched only via question text.
    question = "Will Israel launch a major ground offensive in Lebanon?"
    markets = {
        "tightspiking": _make_market(
            "tightspiking", bid=0.498, ask=0.502,  # 0.4c spread — below 1c threshold, normally filtered
            volume=50_000.0, condition_id="gamma-cid",
        ),
    }
    # Override question to match what Falcon will have
    markets["tightspiking"].question = question

    feeds = FeedState()
    ins = _fresh_insight("falcon-cid", volume_trend="Spiking", whale_control_flag=False)
    ins.question = question
    feeds.falcon_market_insights["falcon-cid"] = ins
    feeds.falcon_insights_by_question[question.strip().lower()] = ins

    selected = MarketSelector.filter_and_rank(markets, feeds=feeds)
    assert "tightspiking" in selected, "question-based fallback should bypass tight spread filter"


@pytest.mark.asyncio
async def test_selector_writes_selected_token_ids_to_maker_state():
    """MarketSelector.run() writes selected_token_ids to MakerState directly."""
    from market.state import AppState
    from maker.state import MakerState

    # Build app_state with enough markets to pass filters
    app_state = AppState()
    markets = {
        f"tok{i}": _make_market(f"tok{i}", volume=20_000.0)
        for i in range(3)
    }
    async with app_state._lock:
        app_state.markets.update(markets)

    active_markets_q = asyncio.Queue()
    maker_state = MakerState()

    selector = MarketSelector(app_state, active_markets_q, maker_state=maker_state)

    # Patch _discover_and_seed to be a no-op (avoids real HTTP calls)
    async def _noop(self=None):
        pass

    with patch.object(selector, "_discover_and_seed", new=AsyncMock(return_value=None)):
        # Run one iteration: run() loops forever with asyncio.sleep — cancel after first put
        async def run_once():
            task = asyncio.create_task(selector.run())
            # Wait until the queue gets the first selection
            result = await asyncio.wait_for(active_markets_q.get(), timeout=5.0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return result

        selected_set = await run_once()

    # selected_token_ids must equal what was put on the queue
    assert isinstance(maker_state.selected_token_ids, set)
    assert maker_state.selected_token_ids == selected_set
