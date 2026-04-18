from datetime import datetime, timedelta, timezone

import market.clob_monitor as cm


def _meta(question: str, category: str, volume: float, hours: int) -> dict:
    return {
        "question": question,
        "category": category,
        "expiry": datetime.now(timezone.utc) + timedelta(hours=hours),
        "no_token_id": f"{question}_no",
        "volume": volume,
        "parseable": category != "unknown",
        "best_bid": 0.4,
        "best_ask": 0.6,
    }


def test_select_markets_volume_desc(monkeypatch):
    # "c" is filtered out (macro not in "crypto,rates").
    # "d" expires in 2h  -> short_dated_bonus=-3 (wins over higher-volume peers)
    # "b" expires in 48h -> short_dated_bonus=-2, volume=900
    # "a" expires in 96h -> short_dated_bonus=0,  volume=500
    # Top 2 by sort: "d" first (tightest bonus), then "b" (bonus -2 beats 0)
    monkeypatch.setattr(cm, "MARKET_CATEGORY_FILTER", "crypto,rates")
    monkeypatch.setattr(cm, "MARKET_SORT_MODE", "volume_desc")
    monkeypatch.setattr(cm, "MAX_SUBSCRIBED_MARKETS", 2)
    monkeypatch.setattr(cm, "PARSEABLE_MARKET_RESERVE", 0)
    token_map = {
        "a": _meta("A", "crypto", 500, 96),
        "b": _meta("B", "rates", 900, 48),
        "c": _meta("C", "macro", 2000, 1),
        "d": _meta("D", "crypto", 700, 2),
    }

    selected = cm.select_markets(token_map)

    assert list(selected) == ["d", "b"]


def test_select_markets_expiry_asc(monkeypatch):
    monkeypatch.setattr(cm, "MARKET_CATEGORY_FILTER", "crypto,rates,macro")
    monkeypatch.setattr(cm, "MARKET_SORT_MODE", "expiry_asc")
    monkeypatch.setattr(cm, "MAX_SUBSCRIBED_MARKETS", 2)
    monkeypatch.setattr(cm, "PARSEABLE_MARKET_RESERVE", 0)
    token_map = {
        "a": _meta("A", "crypto", 500, 24),
        "b": _meta("B", "rates", 900, 6),
        "c": _meta("C", "macro", 2000, 1),
    }

    selected = cm.select_markets(token_map)

    assert list(selected) == ["c", "b"]


def test_select_markets_empty_filter_keeps_unknown(monkeypatch):
    monkeypatch.setattr(cm, "MARKET_CATEGORY_FILTER", "")
    monkeypatch.setattr(cm, "MARKET_SORT_MODE", "volume24h_desc")
    monkeypatch.setattr(cm, "MAX_SUBSCRIBED_MARKETS", 2)
    monkeypatch.setattr(cm, "PARSEABLE_MARKET_RESERVE", 0)
    token_map = {
        "a": {
            **_meta("A", "unknown", 500, 24),
            "volume_24h": 200,
            "liquidity": 1000,
        },
        "b": {
            **_meta("B", "crypto", 700, 48),
            "volume_24h": 100,
            "liquidity": 800,
        },
        "c": {
            **_meta("C", "unknown", 300, 12),
            "volume_24h": 300,
            "liquidity": 1200,
        },
    }

    selected = cm.select_markets(token_map)

    assert list(selected) == ["c", "a"]


def test_select_markets_parseable_reserve_150(monkeypatch):
    """PARSEABLE_MARKET_RESERVE=150 should allow up to 150 parseable slots."""
    monkeypatch.setattr(cm, "MARKET_CATEGORY_FILTER", "")
    monkeypatch.setattr(cm, "MARKET_SORT_MODE", "volume_desc")
    monkeypatch.setattr(cm, "MAX_SUBSCRIBED_MARKETS", 200)
    monkeypatch.setattr(cm, "PARSEABLE_MARKET_RESERVE", 150)

    # Build 200 parseable + 50 non-parseable markets
    token_map = {}
    for i in range(200):
        token_map[f"parseable_{i}"] = {
            **_meta(f"Parseable {i}", "crypto", float(200 - i), 100),
            "parseable": True,
            "volume_24h": float(200 - i),
            "liquidity": float(200 - i),
        }
    for i in range(50):
        token_map[f"other_{i}"] = {
            **_meta(f"Other {i}", "unknown", float(1000 + i), 100),
            "parseable": False,
            "volume_24h": float(1000 + i),
            "liquidity": float(1000 + i),
        }

    selected = cm.select_markets(token_map)

    # Total is capped at MAX_SUBSCRIBED_MARKETS=200
    assert len(selected) == 200
    # At least 150 parseable slots should be filled
    parseable_selected = [k for k in selected if k.startswith("parseable_")]
    assert len(parseable_selected) >= 150


def test_sort_key_short_dated_bonus(monkeypatch):
    """Markets expiring in 48h should sort above markets expiring in 90 days."""
    monkeypatch.setattr(cm, "MARKET_SORT_MODE", "volume_desc")

    meta_short = _meta("Short expiry", "crypto", 100.0, 48)
    meta_long = _meta("Long expiry", "crypto", 5000.0, 2160)  # 90 days

    key_short = cm._sort_key(meta_short)
    key_long = cm._sort_key(meta_long)

    # Short-dated gets bonus (lower first element) so it sorts before long-dated
    assert key_short < key_long, (
        f"Short-dated key {key_short} should be less than long-dated key {key_long}"
    )


def test_sort_key_under_24h_beats_48h(monkeypatch):
    """Markets expiring in <24h should sort above markets expiring in 48h."""
    monkeypatch.setattr(cm, "MARKET_SORT_MODE", "volume_desc")

    meta_24h = _meta("Under 24h", "crypto", 100.0, 12)
    meta_48h = _meta("Around 48h", "crypto", 100.0, 48)

    key_24h = cm._sort_key(meta_24h)
    key_48h = cm._sort_key(meta_48h)

    assert key_24h < key_48h, (
        f"<24h key {key_24h} should be less than 48h key {key_48h}"
    )


def test_select_markets_reserves_parseable_slots(monkeypatch):
    monkeypatch.setattr(cm, "MARKET_CATEGORY_FILTER", "")
    monkeypatch.setattr(cm, "MARKET_SORT_MODE", "volume24h_desc")
    monkeypatch.setattr(cm, "MAX_SUBSCRIBED_MARKETS", 3)
    monkeypatch.setattr(cm, "PARSEABLE_MARKET_RESERVE", 2)
    token_map = {
        "a": {
            **_meta("A", "unknown", 500, 24),
            "parseable": False,
            "volume_24h": 1000,
            "liquidity": 1000,
        },
        "b": {
            **_meta("B", "unknown", 700, 24),
            "parseable": False,
            "volume_24h": 900,
            "liquidity": 900,
        },
        "c": {
            **_meta("C", "rates", 100, 24),
            "parseable": True,
            "volume_24h": 50,
            "liquidity": 500,
        },
        "d": {
            **_meta("D", "crypto", 90, 24),
            "parseable": True,
            "volume_24h": 40,
            "liquidity": 400,
        },
    }

    selected = cm.select_markets(token_map)

    assert list(selected) == ["c", "d", "a"]


def test_gamma_fetch_uses_required_params():
    """fetch_active_markets must pass active, closed, enableOrderBook, limit, offset to Gamma API.

    Note: sort=volume24hr is NOT sent — the Gamma API returns 422 for that parameter.
    The 3000-market budget (30 pages × 100) in default ordering covers all categories.
    """
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    import market.clob_monitor as cm_mod

    captured_params = []

    async def _fake_get(url, params=None, timeout=None):
        captured_params.append(dict(params or {}))
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = []   # empty → stops pagination after first page
        return resp

    async def _run():
        client = AsyncMock()
        client.get.side_effect = _fake_get
        with patch.object(cm_mod, "_fetch_weather_event_markets", new=AsyncMock(return_value={})), \
             patch.object(cm_mod, "_fetch_crypto_event_markets", new=AsyncMock(return_value={})):
            await cm_mod.fetch_active_markets(client)

    asyncio.run(_run())

    assert captured_params, "fetch_active_markets made no HTTP calls"
    first = captured_params[0]
    assert first.get("active") == "true"
    assert first.get("enableOrderBook") == "true"
    assert first.get("limit") == 100
    assert first.get("offset") == 0
    # sort/order NOT asserted — Gamma API returns 422 for sort=volume24hr
    assert "sort" not in first, (
        f"Gamma API rejects sort param with 422 — remove it. Got: {first}"
    )


def test_default_subscription_cap_is_500():
    """The default MAX_SUBSCRIBED_MARKETS must be 500 to cover all qualifying maker markets.

    Before the fix: cm.MAX_SUBSCRIBED_MARKETS == 250 → this test FAILS.
    After the fix:  cm.MAX_SUBSCRIBED_MARKETS == 500 → this test PASSES.
    """
    import importlib
    import market.clob_monitor as cm_fresh
    # Reload to ensure we read the module default, not a monkeypatched value
    importlib.reload(cm_fresh)
    assert cm_fresh.MAX_SUBSCRIBED_MARKETS == 500, (
        f"Expected MAX_SUBSCRIBED_MARKETS=500, got {cm_fresh.MAX_SUBSCRIBED_MARKETS}. "
        "Raise the default in config.py line 124."
    )
