# tests/maker/test_runner.py
import asyncio
import pytest

from maker.runner import (
    _extract_heartbeat_id,
    _heartbeat_loop,
    build_maker_actors,
)


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


def test_extract_heartbeat_id_from_dict_and_error_text():
    assert _extract_heartbeat_id({"heartbeat_id": "abc-123"}) == "abc-123"
    assert (
        _extract_heartbeat_id('400 Bad Request {"heartbeat_id":"server-id"}')
        == "server-id"
    )
    assert _extract_heartbeat_id("no heartbeat here") == ""


def test_heartbeat_loop_uses_empty_id_then_latest_id():
    class StopLoop(BaseException):
        pass

    class FakeClob:
        def __init__(self):
            self.calls: list[str] = []

        def post_heartbeat(self, heartbeat_id=""):
            self.calls.append(heartbeat_id)
            if len(self.calls) == 1:
                return {"heartbeat_id": "id-1"}
            if len(self.calls) == 2:
                return {"heartbeat_id": "id-2"}
            raise StopLoop()

    async def _run():
        clob = FakeClob()
        with pytest.raises(StopLoop):
            await _heartbeat_loop(clob, interval=0.001)
        assert clob.calls == ["", "id-1", "id-2"]

    asyncio.run(_run())


def test_heartbeat_loop_updates_expired_id_and_retries_immediately():
    class StopLoop(BaseException):
        pass

    class FakeClob:
        def __init__(self):
            self.calls: list[str] = []

        def post_heartbeat(self, heartbeat_id=""):
            self.calls.append(heartbeat_id)
            if len(self.calls) == 1:
                return {"heartbeat_id": "old-id"}
            if len(self.calls) == 2:
                raise Exception("400 {'heartbeat_id': 'fresh-id'}")
            if len(self.calls) == 3:
                return {"heartbeat_id": "newer-id"}
            raise StopLoop()

    async def _run():
        clob = FakeClob()
        with pytest.raises(StopLoop):
            await _heartbeat_loop(clob, interval=0.001)
        assert clob.calls == ["", "old-id", "fresh-id", "newer-id"]

    asyncio.run(_run())
