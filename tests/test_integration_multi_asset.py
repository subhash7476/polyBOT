import pytest
from market.state import AppState, FeedState, ContractState
from engine.contract_parser import parse_contract
from engine.probability import build_model_probability
from engine.signal_filter import passes_signal_filter
from trading.ev_gate import calculate_ev, should_enter, get_trade_direction
from trading.slippage import estimate_slippage


@pytest.mark.asyncio
async def test_sol_contract_full_pipeline():
    """SOL contract flows through the entire pipeline without errors."""
    state = AppState()

    # Seed feeds
    await state.update_asset_feed("SOL", spot=140.0, dvol=85.0, funding_rate=0.0003)
    state.feeds.dxy_trend = -0.02
    state.feeds.dxy_confidence = 0.8

    # 1. Parse
    parsed = parse_contract("sol_tok_1", "Will SOL be above $200 by end of April?")
    assert parsed.parseable
    assert parsed.asset == "SOL"

    # 2. Build probability
    prob, count, engine = build_model_probability(
        parsed, state.feeds, {"funding_rate": 0.15, "macro_dxy": 0.10}
    )
    assert 0.0 < prob < 1.0

    # 3. Signal filter
    ok, reason = passes_signal_filter(engine, min_signals=1)  # relaxed for test

    # 4. Slippage
    slippage = estimate_slippage(
        side="BUY", size_usdc=50.0,
        best_bid=0.25, best_ask=0.28,
        volume_usd=50000.0,
    )

    # 5. EV gate
    ev, side = calculate_ev(model_prob=prob, market_price=0.26, slippage=slippage)
    assert isinstance(ev, float)
    assert side in ("BUY_YES", "BUY_NO")


@pytest.mark.asyncio
async def test_xrp_contract_full_pipeline():
    """XRP contract flows through pipeline."""
    state = AppState()
    await state.update_asset_feed("XRP", spot=2.0, dvol=90.0, funding_rate=-0.0001)

    parsed = parse_contract("xrp_tok_1", "Will XRP be above $5 by end of May?")
    assert parsed.parseable
    assert parsed.asset == "XRP"

    prob, count, engine = build_model_probability(
        parsed, state.feeds, {"funding_rate": 0.15}
    )
    # XRP at $2, target $5 — prior should be low
    assert prob < 0.3


@pytest.mark.asyncio
async def test_multi_asset_feeds_isolated():
    """BTC and SOL feeds don't interfere with each other."""
    state = AppState()
    await state.update_asset_feed("BTC", spot=85000.0, dvol=72.0)
    await state.update_asset_feed("SOL", spot=140.0, dvol=80.0)

    assert state.feeds.spot_prices["BTC"] == 85000.0
    assert state.feeds.spot_prices["SOL"] == 140.0
    assert state.feeds.dvol["BTC"] == 72.0
    assert state.feeds.dvol["SOL"] == 80.0

    # BTC contract reads BTC feeds only
    btc_contract = parse_contract("btc_tok", "Will BTC be above $90,000 by March?")
    btc_prob, _, _ = build_model_probability(btc_contract, state.feeds, {})

    # SOL contract reads SOL feeds only
    sol_contract = parse_contract("sol_tok", "Will SOL be above $200 by April?")
    sol_prob, _, _ = build_model_probability(sol_contract, state.feeds, {})

    # Both should produce valid probabilities, not 0.5 fallback
    assert btc_prob != 0.5
    assert sol_prob != 0.5
