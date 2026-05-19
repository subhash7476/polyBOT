from datetime import datetime, timezone, timedelta

from maker.market_selector import MarketSelector
from maker.state import MakerState
from market.state import ContractState


def _cs(token: str, category: str, bid: float, ask: float, hours: float) -> ContractState:
    expiry = datetime.now(timezone.utc) + timedelta(hours=hours)
    return ContractState(
        yes_token_id=token,
        no_token_id=f"no_{token}",
        question=f"{category} {token}",
        category=category,
        best_bid=bid,
        best_ask=ask,
        volume_usd=5000.0,
        volume_24h=5000.0,
        end_date_iso=expiry.isoformat(),
    )


def test_equal_weight_category_selection_spreads_slots_across_categories():
    markets = {
        "w1": _cs("w1", "weather", 0.45, 0.55, 48),
        "w2": _cs("w2", "weather", 0.46, 0.56, 48),
        "s1": _cs("s1", "sports", 0.44, 0.54, 48),
        "s2": _cs("s2", "sports", 0.43, 0.53, 48),
        "e1": _cs("e1", "event", 0.47, 0.57, 48),
        "e2": _cs("e2", "event", 0.48, 0.58, 48),
    }

    selected = MarketSelector.filter_and_rank(
        markets,
        max_markets=3,
        maker_state=MakerState(),
    )

    cats = {cs.category for cs in selected.values()}
    assert len(selected) == 3
    assert cats == {"weather", "sports", "event"}
