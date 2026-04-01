# tests/maker/test_integration.py
"""Integration test: full actor pipeline in paper mode."""

import asyncio
import time
from market.state import AppState, ContractState
from maker.runner import build_maker_actors
from maker.types import Fill
from maker.quote_engine import compute_fair_value, compute_spread, QuoteEngine


def test_full_pipeline_paper_mode():
    """
    Simulate: selector picks a market → quote engine quotes it →
    fill poller detects a paper fill → inventory updates.
    """

    async def _run():
        app_state = AppState()

        # Seed a sports market into state
        async with app_state._lock:
            app_state.markets["tok1"] = ContractState(
                yes_token_id="tok1",
                no_token_id="no-tok1",
                question="Will the Lakers beat the Celtics?",
                category="sports",
                best_bid=0.40,
                best_ask=0.60,
                volume_usd=5_000.0,
            )

        actors, queues = build_maker_actors(app_state=app_state, paper=True)

        # 1. Manually trigger market selection
        selected = actors["selector"].filter_and_rank(app_state.markets)
        assert "tok1" in selected

        # 2. Push selected markets to quote engine
        await queues["active_markets_q"].put(set(selected.keys()))

        # Test the components directly (rather than running async loops):
        fv = compute_fair_value(mid=0.50, skew=0.0, model_adj=0.0)
        spread = compute_spread(volume_usd=5_000.0, abs_inventory=0.0, hours_to_expiry=100.0)
        quote = QuoteEngine.build_quote("tok1", fv, spread, 10.0, 10.0, "new_market")

        assert quote.bid_price < quote.ask_price
        assert quote.spread >= 0.04

        # 3. Simulate order placement via OrderManager
        actors["order_manager"].handle_quote_intent_sync(quote)
        maker_state = actors["order_manager"]._maker
        assert "tok1" in maker_state.live_orders

        # 4. Simulate a fill via InventoryManager
        fill = Fill("tok1", "BUY", quote.bid_price, 10.0, "paper-bid", time.time())
        await actors["inventory"].handle_fill(fill)

        assert maker_state.get_inventory("tok1") == 10.0
        skew = await queues["skew_updates_q"].get()
        assert skew.skew_factor > 0  # holding YES → positive skew

    asyncio.run(_run())
