import asyncio
from unittest.mock import MagicMock
from datetime import datetime, timezone, timedelta
from market.state import ContractState
from maker.market_selector import MarketSelector


def _make_market(
    token_id: str,
    category: str = "sports",
    bid: float = 0.40,
    ask: float = 0.60,
    volume: float = 5_000.0,
) -> ContractState:
    return ContractState(
        yes_token_id=token_id,
        no_token_id=f"no-{token_id}",
        question=f"Will team A beat team B? [{token_id}]",
        category=category,
        best_bid=bid,
        best_ask=ask,
        volume_usd=volume,
    )


def test_filters_non_sports_or_event():
    markets = {
        "crypto1": _make_market("crypto1", category="crypto"),
        "sports1": _make_market("sports1", category="sports"),
        "event1":  _make_market("event1",  category="event"),
    }
    selected = MarketSelector.filter_and_rank(markets)
    assert "sports1" in selected
    assert "event1" in selected
    assert "crypto1" not in selected


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
        "tight": _make_market("tight", bid=0.49, ask=0.51),  # 2c spread
        "wide":  _make_market("wide",  bid=0.40, ask=0.60),  # 20c spread
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
