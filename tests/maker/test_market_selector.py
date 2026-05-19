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
    volume: float = 50_000.0,
    condition_id: str = "",
    end_date_iso: str = "",
) -> ContractState:
    from datetime import datetime, timezone, timedelta
    _end_date = end_date_iso or (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    return ContractState(
        yes_token_id=token_id,
        no_token_id=f"no-{token_id}",
        question=f"Will team A beat team B? [{token_id}]",
        category=category,
        best_bid=bid,
        best_ask=ask,
        volume_usd=volume,
        volume_24h=volume,
        end_date_iso=_end_date,
        condition_id=condition_id or token_id,
    )


def test_no_category_excluded():
    """MarketSelector is fully category-agnostic — every category (including
    'unknown' from parse_contract() misses) is eligible to quote."""
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
    assert "unknown1"  in selected


def test_filters_low_volume():
    markets = {
        "thin": _make_market("thin", volume=50.0),
        "ok":   _make_market("ok",   volume=15_000.0),
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
        "a": _make_market("a", bid=0.40, ask=0.50, volume=10_000.0),  # score=1000
        "b": _make_market("b", bid=0.40, ask=0.60, volume=20_000.0),  # score=4000
    }
    selected = MarketSelector.filter_and_rank(markets)
    keys = list(selected.keys())
    assert keys[0] == "b"  # higher score first


def test_fee_enabled_market_beats_fee_free_tie():
    markets = {
        "fee": _make_market("fee", bid=0.40, ask=0.60, volume=20_000.0),
        "free": _make_market("free", bid=0.40, ask=0.60, volume=20_000.0),
    }
    markets["fee"].fees_enabled = True
    markets["free"].fees_enabled = False

    selected = MarketSelector.filter_and_rank(markets)
    assert list(selected.keys())[0] == "fee"


def test_confirmed_incentive_metadata_boosts_market():
    markets = {
        "rewarded": _make_market("rewarded", bid=0.40, ask=0.60, volume=10_000.0),
        "plain": _make_market("plain", bid=0.40, ask=0.60, volume=11_000.0),
    }
    markets["rewarded"].min_incentive_size = 5.0
    markets["rewarded"].max_incentive_spread = 0.03

    selected = MarketSelector.filter_and_rank(markets)
    assert list(selected.keys())[0] == "rewarded"


def test_caps_at_max_active():
    markets = {
        f"m{i}": _make_market(f"m{i}", volume=float(100_000 - i))
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
        "a": _make_market("a", bid=0.40, ask=0.50, volume=10_000.0),  # base=1000
        "b": _make_market("b", bid=0.40, ask=0.60, volume=20_000.0),  # base=4000
    }
    selected = MarketSelector.filter_and_rank(markets, feeds=None)
    assert list(selected.keys())[0] == "b"


def test_falcon_whale_control_demotes_market():
    """A whale-controlled market (×0.3) should rank below a normal market."""
    markets = {
        "whale": _make_market("whale", bid=0.40, ask=0.60, volume=20_000.0, condition_id="cwhale"),
        "clean": _make_market("clean", bid=0.40, ask=0.60, volume=10_000.0, condition_id="cclean"),
    }
    feeds = FeedState()
    feeds.falcon_market_insights["cwhale"] = _fresh_insight("cwhale", whale_control_flag=True)
    feeds.falcon_market_insights["cclean"] = _fresh_insight("cclean")

    selected = MarketSelector.filter_and_rank(markets, feeds=feeds)
    keys = list(selected.keys())
    # whale has 2× volume but 0.3× Falcon mult → score=1200; clean → score=2000
    assert keys[0] == "clean"


def test_falcon_spiking_trend_boosts_market():
    """Spiking volume trend (×1.5) should lift a market above a larger normal one."""
    markets = {
        "spike": _make_market("spike", bid=0.40, ask=0.60, volume=10_000.0, condition_id="cspike"),
        "normal": _make_market("normal", bid=0.40, ask=0.60, volume=12_000.0, condition_id="cnorm"),
    }
    feeds = FeedState()
    feeds.falcon_market_insights["cspike"] = _fresh_insight("cspike", volume_trend="Spiking")
    feeds.falcon_market_insights["cnorm"] = _fresh_insight("cnorm", volume_trend="Normal")

    selected = MarketSelector.filter_and_rank(markets, feeds=feeds)
    keys = list(selected.keys())
    # spike: 10000 × 0.20 × 1.5 = 3000; normal: 12000 × 0.20 × 1.0 = 2400
    assert keys[0] == "spike"


def test_falcon_dying_interest_strongly_demotes():
    """Dying Interest (×0.2) drops a high-volume market far down."""
    markets = {
        "dying": _make_market("dying", bid=0.40, ask=0.60, volume=20_000.0, condition_id="cdying"),
        "ok":    _make_market("ok",    bid=0.40, ask=0.60, volume=10_000.0, condition_id="cok"),
    }
    feeds = FeedState()
    feeds.falcon_market_insights["cdying"] = _fresh_insight("cdying", volume_trend="Dying Interest")
    feeds.falcon_market_insights["cok"] = _fresh_insight("cok")

    selected = MarketSelector.filter_and_rank(markets, feeds=feeds)
    keys = list(selected.keys())
    # dying: 20000 × 0.20 × 0.2 = 800; ok: 10000 × 0.20 × 1.0 = 2000
    assert keys[0] == "ok"


def test_falcon_stale_insight_ignored():
    """Stale Falcon data returns neutral (1.0×), so base score is used unchanged."""
    markets = {
        "m": _make_market("m", bid=0.40, ask=0.60, volume=50_000.0, condition_id="cm"),
    }
    feeds = FeedState()
    feeds.falcon_market_insights["cm"] = _fresh_insight(
        "cm", whale_control_flag=True, fetched_at=time.time() - 99999
    )
    selected = MarketSelector.filter_and_rank(markets, feeds=feeds)
    assert "m" in selected  # stale → not excluded, neutral mult applied


def test_falcon_spiking_bypasses_tight_spread_filter():
    """Falcon-spiking markets with tight spread are excluded by filter_and_rank().

    Force 3 seeds them into state for diagnostic visibility (DIAG log), but
    filter_and_rank() still enforces _MIN_SPREAD — no bypass is implemented.
    """
    markets = {
        "tight_spiking": _make_market(
            "tight_spiking", bid=0.498, ask=0.502,  # 0.4c spread — below 1c threshold
            volume=50_000.0, condition_id="cspike",
        ),
        "wide_normal": _make_market(
            "wide_normal", bid=0.40, ask=0.60, volume=50_000.0, condition_id="cnorm",
        ),
    }
    feeds = FeedState()
    feeds.falcon_market_insights["cspike"] = _fresh_insight(
        "cspike", volume_trend="Spiking", whale_control_flag=False,
    )
    feeds.falcon_market_insights["cnorm"] = _fresh_insight("cnorm", volume_trend="Normal")

    selected = MarketSelector.filter_and_rank(markets, feeds=feeds)
    assert "tight_spiking" not in selected  # excluded by tight_spread filter; no bypass in filter_and_rank
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
        "diverse": _make_market("diverse", bid=0.40, ask=0.60, volume=50_000.0, condition_id="cdiv"),
        "sparse":  _make_market("sparse",  bid=0.40, ask=0.60, volume=50_000.0, condition_id="cspa"),
    }
    feeds = FeedState()
    feeds.falcon_market_insights["cdiv"] = _fresh_insight("cdiv", unique_traders_7d=2000)
    feeds.falcon_market_insights["cspa"] = _fresh_insight("cspa", unique_traders_7d=100)

    selected = MarketSelector.filter_and_rank(markets, feeds=feeds)
    keys = list(selected.keys())
    assert keys[0] == "diverse"


def test_falcon_question_fallback_bypasses_tight_spread():
    """Tight-spread spiking markets are excluded even via question-based fallback.

    filter_and_rank() enforces _MIN_SPREAD unconditionally — no bypass is implemented.
    Force 3 seeds such markets into state for DIAG visibility; they are still excluded
    at the tight_spread filter regardless of how the Falcon insight is matched.
    """
    # Market in state has condition_id "gamma-cid"; Falcon insight has "falcon-cid"
    # — two different IDs for the same market, matched only via question text.
    question = "Will Israel launch a major ground offensive in Lebanon?"
    markets = {
        "tightspiking": _make_market(
            "tightspiking", bid=0.498, ask=0.502,  # 0.4c spread — below 1c threshold
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
    assert "tightspiking" not in selected, "tight spread still excludes even with Spiking Falcon match"


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


# ── Fix 2: no-date exclusion ──────────────────────────────────────────────────

def test_no_end_date_excluded_by_default(monkeypatch):
    """Market with empty end_date_iso must be excluded by default (env var absent)."""
    import importlib
    monkeypatch.delenv("MAKER_REQUIRE_END_DATE", raising=False)  # ensure default
    import maker.market_selector as ms_mod
    importlib.reload(ms_mod)
    # Explicitly pass empty end_date_iso to test the no-date filter
    cs = _make_market("no_date", bid=0.40, ask=0.60, volume=50_000.0, end_date_iso="")
    cs.end_date_iso = ""  # ensure it's cleared (belt-and-suspenders)
    selected = ms_mod.MarketSelector.filter_and_rank({"no_date": cs})
    assert "no_date" not in selected


def test_no_end_date_allowed_when_require_disabled(monkeypatch):
    """Market with empty end_date_iso passes when MAKER_REQUIRE_END_DATE=false."""
    import importlib
    monkeypatch.setenv("MAKER_REQUIRE_END_DATE", "false")
    import maker.market_selector as ms_mod
    importlib.reload(ms_mod)
    # Explicitly pass empty end_date_iso to test the no-date filter being disabled
    cs = _make_market("no_date", bid=0.40, ask=0.60, volume=50_000.0, end_date_iso="")
    cs.end_date_iso = ""  # ensure it's cleared (belt-and-suspenders)
    selected = ms_mod.MarketSelector.filter_and_rank({"no_date": cs})
    assert "no_date" in selected


def test_market_with_valid_end_date_still_passes(monkeypatch):
    """Market with end_date_iso within the 7-day window must still be selected."""
    from datetime import datetime, timezone, timedelta
    import importlib
    monkeypatch.delenv("MAKER_REQUIRE_END_DATE", raising=False)
    import maker.market_selector as ms_mod
    importlib.reload(ms_mod)
    future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    cs = _make_market("has_date", bid=0.40, ask=0.60, volume=50_000.0)
    cs.end_date_iso = future
    selected = ms_mod.MarketSelector.filter_and_rank({"has_date": cs})
    assert "has_date" in selected


# ── Fix 3: Falcon force-seed ──────────────────────────────────────────────────

def test_discover_and_seed_force_seeds_falcon_spiking_markets(monkeypatch):
    """Falcon spiking markets with tight spreads must be seeded into state
    even if their spread*vol score falls below the normal top-60 window."""
    import asyncio
    import importlib
    from datetime import datetime, timezone, timedelta
    from unittest.mock import AsyncMock, patch
    from market.state import AppState

    monkeypatch.delenv("MAKER_REQUIRE_END_DATE", raising=False)
    import maker.market_selector as ms_mod
    importlib.reload(ms_mod)

    # Wide-spread normal market (fills the top slot normally); give it a valid expiry
    # so it passes the _REQUIRE_END_DATE and far-future guards in _discover_and_seed
    expiry_soon = datetime.now(timezone.utc) + timedelta(days=2)
    wide_meta = {
        "condition_id": "cwide",
        "question": "Will wide market resolve YES?",
        "category": "event",
        "best_bid": 0.40,
        "best_ask": 0.60,
        "volume": 5_000_000.0,
        "volume_24h": 5_000_000.0,
        "no_token_id": "no-wide",
        "expiry": expiry_soon,
        "neg_risk": False,
        "fees_enabled": True,
    }
    # Tight-spread spiking Falcon market (score near-zero, falls below top-60)
    tight_meta = {
        "condition_id": "ctight",
        "question": "US forces enter Iran by April 30?",
        "category": "event",
        "best_bid": 0.499,
        "best_ask": 0.501,
        "volume": 100_000_000.0,
        "volume_24h": 100_000_000.0,
        "no_token_id": "no-tight",
        "expiry": None,
        "neg_risk": False,
        "fees_enabled": True,
    }
    token_map = {"wide-yes": wide_meta, "tight-yes": tight_meta}

    app = AppState()
    # Register a Falcon spiking insight for the tight market
    app.feeds.falcon_market_insights["ctight"] = _fresh_insight(
        "ctight",
        question="US forces enter Iran by April 30?",
        volume_trend="Spiking",
        current_volume_24h=100_000_000.0,
        unique_traders_7d=14668,
        top1_wallet_pct=2.0,
        whale_control_flag=False,
    )

    selector = ms_mod.MarketSelector.__new__(ms_mod.MarketSelector)
    selector._state = app

    with patch("maker.market_selector.fetch_active_markets", new=AsyncMock(return_value=token_map)):
        asyncio.run(selector._discover_and_seed())

    # The tight Falcon spiking market must now be in state
    assert "tight-yes" in app.markets, "Falcon spiking market should be force-seeded"
    # The normal wide market should also be seeded
    assert "wide-yes" in app.markets


def test_discover_seed_uses_volume_24h_for_filter(monkeypatch):
    """_discover_and_seed must seed markets with high volume_24h even when
    all-time CLOB volume is zero.

    Bug: line 377 used meta.get("volume", 0) — all-time CLOB volume.
    A new esports market with volume=0 but volume_24h=$50k was silently
    excluded and never entered state.
    """
    import asyncio
    import importlib
    from datetime import datetime, timezone, timedelta
    from unittest.mock import AsyncMock, patch
    from market.state import AppState

    monkeypatch.delenv("MAKER_REQUIRE_END_DATE", raising=False)
    import maker.market_selector as ms_mod
    importlib.reload(ms_mod)

    expiry_soon = datetime.now(timezone.utc) + timedelta(days=2)
    new_market_meta = {
        "condition_id": "cnew",
        "question": "Will FURIA win IEM Rio 2026?",
        "category": "sports",
        "best_bid": 0.45,
        "best_ask": 0.52,       # 7c spread — passes spread filter
        "volume": 0.0,           # zero all-time CLOB volume → fails OLD filter
        "volume_24h": 50_000.0,  # $50k 24h volume → must pass NEW filter
        "no_token_id": "no-furia",
        "expiry": expiry_soon,
        "neg_risk": False,
        "fees_enabled": True,
    }

    app = AppState()
    selector = ms_mod.MarketSelector.__new__(ms_mod.MarketSelector)
    selector._state = app

    with patch(
        "maker.market_selector.fetch_active_markets",
        new=AsyncMock(return_value={"furia-yes": new_market_meta}),
    ):
        asyncio.run(selector._discover_and_seed())

    assert "furia-yes" in app.markets, (
        "Market with volume_24h=$50k but volume=0 must be seeded. "
        "Fix: use (meta.get('volume_24h', 0) or meta.get('volume', 0)) at line 377."
    )


def test_min_bid_excludes_low_probability_markets():
    """filter_and_rank must exclude markets with best_bid < 0.10.

    Bug: _MIN_BID=0.05 allowed markets priced at 7-8¢ to enter the active set.
    These near-zero markets attract one-directional taker flow (adverse selection)
    because takers have strong information about the near-certain NO outcome.
    Raising to 0.10 keeps a safe buffer from the resolution boundary.
    """
    import importlib
    import maker.market_selector as ms_mod
    importlib.reload(ms_mod)

    markets = {
        "low_prob":  _make_market("low_prob",  bid=0.08, ask=0.12),   # bid=0.08 < 0.10 → excluded
        "borderline": _make_market("borderline", bid=0.10, ask=0.14), # bid=0.10 = new floor → included
        "normal":    _make_market("normal",    bid=0.40, ask=0.60),   # well within range → included
    }
    selected = ms_mod.MarketSelector.filter_and_rank(markets)
    assert "low_prob" not in selected, (
        "Market with bid=0.08 must be excluded. Raise _MIN_BID to 0.10."
    )
    assert "borderline" in selected, "Market with bid=0.10 must be included."
    assert "normal" in selected, "Normal market must be included."


@pytest.mark.asyncio
async def test_orphaned_market_added_to_reduce_only():
    """When a market leaves selection and has non-zero inventory, it goes to reduce_only_markets."""
    from maker.market_selector import MarketSelector
    from maker.state import MakerState
    from market.state import AppState, FeedState

    maker_state = MakerState()
    maker_state.update_inventory("tok_old", "BUY", 10.0)

    app_state = AppState()
    app_state.feeds = FeedState()
    # Seed one market in state so MarketSelector can run
    app_state.markets["tok_new"] = _make_market("tok_new")

    active_q = asyncio.Queue()
    sel = MarketSelector(app_state, active_q, maker_state=maker_state)

    # Simulate previous selection containing tok_old
    maker_state.selected_token_ids = {"tok_old"}

    # Run one iteration — tok_old won't be in app_state.markets so it won't be selected
    await sel._run_one_cycle()

    assert "tok_old" in maker_state.reduce_only_markets


@pytest.mark.asyncio
async def test_closed_orphan_removed_from_reduce_only():
    """When orphaned position reaches zero inventory, it is removed from reduce_only_markets."""
    from maker.market_selector import MarketSelector
    from maker.state import MakerState
    from market.state import AppState, FeedState

    maker_state = MakerState()
    maker_state.reduce_only_markets.add("tok_closed")
    # inventory is already zero for tok_closed

    app_state = AppState()
    app_state.feeds = FeedState()
    app_state.markets["tok_new"] = _make_market("tok_new")

    active_q = asyncio.Queue()
    sel = MarketSelector(app_state, active_q, maker_state=maker_state)
    maker_state.selected_token_ids = set()

    await sel._run_one_cycle()

    assert "tok_closed" not in maker_state.reduce_only_markets
