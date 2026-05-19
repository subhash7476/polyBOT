import pytest
from maker.quote_engine import QuoteEngine, compute_fair_value, compute_spread, compute_quote_size
from maker.types import LadderUpdate, QuoteIntent


def test_fair_value_at_midpoint_no_skew():
    fv = compute_fair_value(mid=0.50, inventory=0.0, model_adj=0.0)
    assert fv == 0.50


def test_long_position_lowers_fair_value():
    """Long inventory must lower fv to lean against the book and encourage selling."""
    fv_flat = compute_fair_value(mid=0.50, inventory=0.0, model_adj=0.0)
    fv_long = compute_fair_value(mid=0.50, inventory=10.0, model_adj=0.0)
    assert fv_long < fv_flat
    assert fv_long == pytest.approx(0.46)   # 0.50 - 10*0.004


def test_short_position_raises_fair_value():
    """Short inventory must raise fv to attract buyers and close the position."""
    fv_flat = compute_fair_value(mid=0.50, inventory=0.0, model_adj=0.0)
    fv_short = compute_fair_value(mid=0.50, inventory=-10.0, model_adj=0.0)
    assert fv_short > fv_flat
    assert fv_short == pytest.approx(0.54)  # 0.50 + 10*0.004


def test_fair_value_clamped():
    # Large short position hits MAX_SKEW_ABS=0.10 cap then clamps to 0.95
    fv = compute_fair_value(mid=0.87, inventory=-50.0, model_adj=0.0)
    assert fv == pytest.approx(0.95)


def test_spread_base():
    s = compute_spread(volume_usd=1000.0, abs_inventory=0.0, hours_to_expiry=100.0)
    assert s == 0.06


def test_spread_widens_low_volume():
    s = compute_spread(volume_usd=200.0, abs_inventory=0.0, hours_to_expiry=100.0)
    assert s == 0.08


def test_spread_widens_with_inventory():
    # dvol=60 (baseline) -> vol_mult=1.0. spread = 0.06 + 10*0.01 = 0.16. Max spread is 0.15.
    s = compute_spread(volume_usd=1000.0, abs_inventory=10.0, hours_to_expiry=100.0, dvol=60.0)
    assert s == 0.15


def test_spread_widens_near_expiry():
    # dvol=60 -> vol_mult=1.0. spread = 0.06 + 0.02 (24h) = 0.08.
    s = compute_spread(volume_usd=1000.0, abs_inventory=0.0, hours_to_expiry=24.0, dvol=60.0)
    assert s == 0.08


def test_spread_max_very_near_expiry():
    s = compute_spread(volume_usd=1000.0, abs_inventory=0.0, hours_to_expiry=3.0)
    assert s == 0.15


def test_spread_floor():
    s = compute_spread(volume_usd=10000.0, abs_inventory=0.0, hours_to_expiry=500.0)
    assert s >= 0.04


def test_quote_size_defaults_to_baseline():
    size = compute_quote_size(market_size_hint=5.0, regime_size_multiplier=1.0, min_incentive_size=0.0)
    assert size == 5.0


def test_quote_size_floors_to_reward_minimum():
    size = compute_quote_size(market_size_hint=5.0, regime_size_multiplier=1.0, min_incentive_size=50.0)
    assert size == 50.0


def test_quote_size_respects_larger_market_minimum():
    size = compute_quote_size(market_size_hint=5.0, regime_size_multiplier=1.0, min_incentive_size=100.0)
    assert size == 100.0


def test_quote_size_caps_reward_outlier():
    size = compute_quote_size(market_size_hint=5.0, regime_size_multiplier=1.0, min_incentive_size=500.0)
    assert size == 200.0


def test_build_ladder_returns_correct_number_of_levels():
    lu = QuoteEngine.build_ladder(
        token_id="abc",
        fair_value=0.50,
        spread=0.06,
        size=10.0,
        reason="reprice",
    )
    assert isinstance(lu, LadderUpdate)
    assert len(lu.levels) == 3  # LADDER_LEVELS


def test_build_ladder_center_level():
    """Center level (index 1) should be centered on fair value."""
    lu = QuoteEngine.build_ladder(
        token_id="abc",
        fair_value=0.50,
        spread=0.06,
        size=10.0,
        reason="reprice",
    )
    center = lu.center
    assert center.bid_price == round(0.50 - 0.03, 4)   # fv - half_spread
    assert center.ask_price == round(0.50 + 0.03, 4)


def test_build_ladder_outer_levels_wider():
    """Outer levels are offset by LEVEL_STEP from center."""
    lu = QuoteEngine.build_ladder(
        token_id="abc",
        fair_value=0.50,
        spread=0.06,
        size=10.0,
        reason="reprice",
    )
    inner = lu.levels[1]   # center
    outer = lu.levels[0]   # one step below center
    assert outer.bid_price < inner.bid_price
    assert outer.ask_price > inner.ask_price


def test_build_ladder_token_id_on_all_levels():
    lu = QuoteEngine.build_ladder("tok1", 0.50, 0.06, 10.0, "reprice")
    assert all(qi.token_id == "tok1" for qi in lu.levels)


def test_book_relative_quoting_tight_market():
    """When market spread < our spread, eff_spread compresses to match the book.

    For a 2c spread market (best_bid=0.49, best_ask=0.51):
      - Our BASE_SPREAD=0.06 would post bid=0.47 (outside the book)
      - Book-relative: eff_spread=0.02, eff_fv=0.50
      - Center bid = 0.50 - 0.01 = 0.49 = best_bid  → fill condition >= works
    """
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from maker.quote_engine import QuoteEngine, BASE_SPREAD
    from maker.state import MakerState
    from market.state import AppState, ContractState

    async def _run():
        app = AppState()
        cs = ContractState(
            yes_token_id="t1", no_token_id="n1",
            question="test", category="event",
            best_bid=0.49, best_ask=0.51,  # 2c spread < BASE_SPREAD (6c)
            volume_usd=5000.0, condition_id="",
        )
        async with app._lock:
            app.markets["t1"] = cs

        maker = MakerState()
        active_q = asyncio.Queue()
        intent_q = asyncio.Queue()
        skew_q = asyncio.Queue()
        price_q = asyncio.Queue()

        qe = QuoteEngine(app, maker, active_q, intent_q, skew_q, price_update_q=price_q)
        qe._active_token_ids = {"t1"}

        await qe._reprice({"t1"}, force=True, new_ids={"t1"})
        assert not intent_q.empty(), "should have emitted a ladder"
        ladder = intent_q.get_nowait()
        # The CENTER level (tightest toward mid) must be inside the book so the
        # fill poller's >= condition can fire.  Outer ladder levels may step
        # outside — that's by design (they provide depth at wider prices).
        center = ladder.center
        assert center.bid_price >= 0.49, (
            f"center bid {center.bid_price} is outside the book (best_bid=0.49)"
        )
        assert center.ask_price <= 0.51, (
            f"center ask {center.ask_price} is outside the book (best_ask=0.51)"
        )
        # At least one level must be fill-eligible (bid >= best_bid)
        eligible = [lv for lv in ladder.levels if lv.bid_price >= 0.49]
        assert eligible, "no fill-eligible bid levels"

    asyncio.run(_run())


def test_is_stale_returns_false_when_same():
    old = QuoteIntent("abc", 0.45, 0.55, 10.0, 10.0, "reprice")
    new = QuoteIntent("abc", 0.45, 0.55, 10.0, 10.0, "reprice")
    assert not QuoteEngine.is_stale(old, new, tick=0.01)


def test_is_stale_returns_true_when_price_changed():
    old = QuoteIntent("abc", 0.45, 0.55, 10.0, 10.0, "reprice")
    new = QuoteIntent("abc", 0.47, 0.57, 10.0, 10.0, "reprice")
    assert QuoteEngine.is_stale(old, new, tick=0.01)


import asyncio
from maker.state import MakerState
from maker.types import CancelAll
from market.state import AppState, ContractState


def _make_app_for_guard(token_id: str, best_bid: float, best_ask: float) -> AppState:
    app = AppState()
    cs = ContractState(
        yes_token_id=token_id,
        no_token_id="no-" + token_id,
        question="Guard test market",
        category="sports",
        best_bid=best_bid,
        best_ask=best_ask,
        volume_usd=50_000.0,
        end_date_iso="2026-05-01T12:00:00Z",
    )
    app.markets[token_id] = cs
    return app


@pytest.mark.asyncio
async def test_reprice_emits_cancel_for_near_zero_market():
    """_reprice emits CancelAll(token_id) and no LadderUpdate when best_bid < 0.05."""
    token_id = "dead-tok"
    app = _make_app_for_guard(token_id, best_bid=0.03, best_ask=0.06)
    ms = MakerState()
    ms.live_orders[token_id] = [{
        "bid_price": 0.06, "ask_price": 0.10,
        "bid_size": 10.0, "ask_size": 10.0,
        "bid_order_id": "b1", "ask_order_id": "a1",
    }]

    quote_q = asyncio.Queue()
    cancel_q = asyncio.Queue()

    qe = QuoteEngine(app, ms, asyncio.Queue(), quote_q, asyncio.Queue(), cancel_q=cancel_q)
    qe._active_token_ids = {token_id}

    await qe._reprice({token_id}, force=True, new_ids=set())

    assert quote_q.empty(), "No LadderUpdate should be emitted for near-dead market"
    cancel = cancel_q.get_nowait()
    assert isinstance(cancel, CancelAll)
    assert cancel.token_id == token_id
    assert not cancel.is_global


@pytest.mark.asyncio
async def test_reprice_only_warns_once_per_market():
    """_stale_skip_warned tracks warned markets; no duplicate entries."""
    token_id = "dead-tok-2"
    app = _make_app_for_guard(token_id, best_bid=0.02, best_ask=0.04)
    ms = MakerState()
    ms.live_orders[token_id] = []

    qe = QuoteEngine(app, ms, asyncio.Queue(), asyncio.Queue(), asyncio.Queue(),
                     cancel_q=asyncio.Queue())
    qe._active_token_ids = {token_id}

    assert token_id not in qe._stale_skip_warned
    await qe._reprice({token_id}, force=True, new_ids=set())
    assert token_id in qe._stale_skip_warned

    # Second call must not raise; warned set stays singleton
    await qe._reprice({token_id}, force=True, new_ids=set())
    assert qe._stale_skip_warned == {token_id}


@pytest.mark.asyncio
async def test_reprice_skips_market_at_inventory_cap():
    """_reprice emits no LadderUpdate when position is at or above max_inventory_per_market.

    Bug: previously the only gate was in_cooldown() (time-based). After the 300s cooldown
    expired, _reprice would immediately re-quote and produce new fills, growing inventory
    indefinitely. The fix adds a hard position-size gate regardless of cooldown state.
    """
    from market.state import ContractState

    token_id = "capped-tok"
    app = AppState()
    app.markets[token_id] = ContractState(
        yes_token_id=token_id, no_token_id="no-capped",
        question="Capped market", category="sports",
        best_bid=0.45, best_ask=0.55,
        volume_usd=5000.0, end_date_iso="2026-05-01T12:00:00Z",
    )

    maker = MakerState(max_inventory_per_market=50.0)
    maker.inventory[token_id] = 50.0  # exactly at cap

    quote_q = asyncio.Queue()
    qe = QuoteEngine(app, maker, asyncio.Queue(), quote_q, asyncio.Queue())
    qe._active_token_ids = {token_id}

    await qe._reprice({token_id}, force=True, new_ids={token_id})

    assert quote_q.empty(), (
        "No LadderUpdate should be emitted when inventory is at or above the per-market cap"
    )


# ── Regime score integration ────────────────────────────────────────────────

from maker.regime import compute_regime_score, CATEGORY_SPREAD_MULTIPLIER


def test_long_inventory_produces_negative_regime_skew():
    """Long inventory at 80% cap shades fair value downward via regime skew."""
    d = compute_regime_score(vpin=0.5, markout_30s=0.0, inv_signed=0.8,
                             hours_to_resolution=24.0, category="crypto")
    assert d.skew_adjustment < 0.0
    assert abs(d.skew_adjustment) > 0.001


def test_short_inventory_produces_positive_regime_skew():
    """Short inventory shades fair value upward to encourage buying."""
    d = compute_regime_score(vpin=0.5, markout_30s=0.0, inv_signed=-0.8,
                             hours_to_resolution=24.0, category="crypto")
    assert d.skew_adjustment > 0.0


def test_weather_category_spread_multiplier_is_lowered():
    """Weather remains wider than finance, but the bias is reduced."""
    assert CATEGORY_SPREAD_MULTIPLIER["weather"] / CATEGORY_SPREAD_MULTIPLIER["finance"] == 2.2


def test_neutral_regime_spread_multiplier_is_one():
    """All-neutral inputs -> spread_multiplier approx 1.0 (no regime premium)."""
    d = compute_regime_score(vpin=0.5, markout_30s=0.0, inv_signed=0.0,
                             hours_to_resolution=48.0, category="crypto")
    assert abs(d.spread_multiplier - 1.0) < 0.05


def test_extreme_regime_produces_max_spread_multiplier():
    """Maximum stress inputs -> spread_multiplier > 2.0."""
    d = compute_regime_score(vpin=1.0, markout_30s=-0.10, inv_signed=1.0,
                             hours_to_resolution=0.0, category="crypto")
    assert d.spread_multiplier > 2.0
