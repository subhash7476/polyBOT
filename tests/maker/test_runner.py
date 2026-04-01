# tests/maker/test_runner.py
import asyncio
from maker.runner import build_maker_actors


def test_build_maker_actors_returns_all_components():
    """Smoke test: all actors and queues are wired."""
    from market.state import AppState

    async def _run():
        actors, queues = build_maker_actors(
            app_state=AppState(),
            paper=True,
            clob=None,
        )
        assert "selector" in actors
        assert "quote_engine" in actors
        assert "order_manager" in actors
        assert "fill_poller" in actors
        assert "inventory" in actors
        assert "active_markets_q" in queues
        assert "quote_intents_q" in queues
        assert "fills_q" in queues
        assert "skew_updates_q" in queues
        assert "cancel_q" in queues

    asyncio.run(_run())
