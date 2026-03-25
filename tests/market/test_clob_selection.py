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
    monkeypatch.setattr(cm, "MARKET_CATEGORY_FILTER", "crypto,rates")
    monkeypatch.setattr(cm, "MARKET_SORT_MODE", "volume_desc")
    monkeypatch.setattr(cm, "MAX_SUBSCRIBED_MARKETS", 2)
    monkeypatch.setattr(cm, "PARSEABLE_MARKET_RESERVE", 0)
    token_map = {
        "a": _meta("A", "crypto", 500, 24),
        "b": _meta("B", "rates", 900, 48),
        "c": _meta("C", "macro", 2000, 1),
        "d": _meta("D", "crypto", 700, 2),
    }

    selected = cm.select_markets(token_map)

    assert list(selected) == ["b", "d"]


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
