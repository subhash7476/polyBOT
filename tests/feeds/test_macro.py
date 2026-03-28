import pytest
from datetime import datetime, timezone, timedelta
from feeds.macro import CachedValue
from feeds.macro import MacroFeed


def test_cached_value_fresh_is_not_stale():
    cv = CachedValue(value=104.0, fetched_at=datetime.now(timezone.utc), max_age_seconds=600)
    assert cv.is_stale is False


def test_cached_value_old_is_stale():
    old = datetime.now(timezone.utc) - timedelta(seconds=700)
    cv = CachedValue(value=104.0, fetched_at=old, max_age_seconds=600)
    assert cv.is_stale is True


def test_cached_value_confidence_fresh_is_one():
    cv = CachedValue(value=104.0, fetched_at=datetime.now(timezone.utc), max_age_seconds=600)
    assert cv.confidence > 0.99


def test_cached_value_confidence_old_is_zero():
    old = datetime.now(timezone.utc) - timedelta(seconds=700)
    cv = CachedValue(value=104.0, fetched_at=old, max_age_seconds=600)
    assert cv.confidence == 0.0


def test_cached_value_confidence_half_age():
    half_age = datetime.now(timezone.utc) - timedelta(seconds=300)
    cv = CachedValue(value=104.0, fetched_at=half_age, max_age_seconds=600)
    assert abs(cv.confidence - 0.5) < 0.05


def test_macro_feed_fred_csv_fallback_uses_urllib(monkeypatch):
    class DummyClient:
        async def get(self, *args, **kwargs):
            raise RuntimeError("httpx blocked")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class DummyState:
        async def update_feeds(self, **kwargs):
            return None

    feed = MacroFeed(DummyState())

    def fake_urlopen(req, timeout=15.0):
        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b"DATE,VALUE\n2026-01-01,4.25\n"

        return _Resp()

    monkeypatch.setattr("feeds.macro.urlopen", fake_urlopen)
    # Also patch httpx.AsyncClient so the fresh client created inside _fetch_fred_csv fails
    import feeds.macro as macro_module
    monkeypatch.setattr(macro_module.httpx, "AsyncClient", lambda **kwargs: DummyClient())

    import asyncio
    result = asyncio.run(feed._fetch_fred_csv(DummyClient(), "DFEDTARL"))
    assert "2026-01-01,4.25" in result
