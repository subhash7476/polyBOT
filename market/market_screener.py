"""
market/market_screener.py

Hourly market screener: ranks active markets by edge opportunity.

Scoring factors:
1. Volume (log-scaled): high volume = more liquid = easier to exit
2. Spread: narrower spread = better fill price
3. Time to expiry: moderate time (24-72h) scores highest
4. Parseability: our signal engine has coverage
5. Staleness flag: volume below threshold = harder exit

Run standalone:
    python -m market.market_screener

Or call rank_markets() from any async context.
"""
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from market.state import ContractState
from utils.logger import get_logger

log = get_logger(__name__)

_STALE_VOLUME_THRESHOLD = 5_000


@dataclass
class ScreenedMarket:
    token_id: str
    question: str
    category: str
    score: float
    volume_usd: float
    spread: float
    hours_to_expiry: Optional[float]
    is_parseable: bool
    stale_flag: bool


def score_market(
    cs: ContractState,
    expiry: Optional[datetime],
    is_parseable: bool,
) -> float:
    """
    Compute opportunity score for a single market.
    Higher = better edge opportunity.
    """
    score = 0.0

    # Volume component (log-scale, max ~40)
    if cs.volume_usd > 0:
        score += min(40.0, math.log1p(cs.volume_usd) * 2.5)

    # Spread component (tight spread = +30, wide = 0)
    spread = cs.best_ask - cs.best_bid
    if spread < 0.50:
        score += max(0.0, 30.0 * (1 - spread / 0.50))

    # Time to expiry component (peak at 24-72h window)
    if expiry is not None:
        now = datetime.now(timezone.utc)
        hours_left = (expiry - now).total_seconds() / 3600
        if 12 <= hours_left <= 72:
            score += 20.0
        elif 72 < hours_left <= 168:
            score += 10.0
        elif 0 < hours_left < 12:
            score += 5.0

    # Parseability bonus
    if is_parseable:
        score += 15.0

    return round(score, 2)


def rank_markets(
    markets: dict,
) -> list[ScreenedMarket]:
    """
    Rank markets by opportunity score.
    Input: {token_id: (ContractState, expiry_datetime, is_parseable)}
    """
    screened = []
    now = datetime.now(timezone.utc)

    for token_id, item in markets.items():
        cs, expiry, is_parseable = item
        score = score_market(cs, expiry, is_parseable)

        hours_left = None
        if expiry:
            hours_left = (expiry - now).total_seconds() / 3600

        spread = cs.best_ask - cs.best_bid
        stale = cs.volume_usd < _STALE_VOLUME_THRESHOLD

        screened.append(ScreenedMarket(
            token_id=token_id,
            question=cs.question[:80],
            category=cs.category,
            score=score,
            volume_usd=cs.volume_usd,
            spread=round(spread, 4),
            hours_to_expiry=round(hours_left, 1) if hours_left is not None else None,
            is_parseable=is_parseable,
            stale_flag=stale,
        ))

    screened.sort(key=lambda m: m.score, reverse=True)
    return screened


def print_watchlist(ranked: list[ScreenedMarket], top_n: int = 20):
    print(f"\n{'='*80}")
    print(f"Market Screener — Top {min(top_n, len(ranked))} Opportunities")
    print(f"{'='*80}")
    print(f"{'Score':>6}  {'Vol($)':>10}  {'Spread':>7}  {'Hours':>6}  {'Cat':>6}  Question")
    print("-" * 80)
    for m in ranked[:top_n]:
        stale_marker = " [STALE]" if m.stale_flag else ""
        print(
            f"{m.score:>6.1f}  {m.volume_usd:>10,.0f}  {m.spread:>7.4f}  "
            f"{m.hours_to_expiry or 0:>6.1f}  {m.category:>6}  "
            f"{m.question[:50]}{stale_marker}"
        )
    print(f"{'='*80}\n")


if __name__ == "__main__":
    import asyncio
    import httpx
    from market.clob_monitor import fetch_active_markets, select_markets

    async def _run():
        async with httpx.AsyncClient() as client:
            token_map = await fetch_active_markets(client)
        token_map = select_markets(token_map)

        market_inputs = {}
        for token_id, meta in token_map.items():
            cs = ContractState(
                yes_token_id=token_id,
                no_token_id=meta["no_token_id"],
                question=meta["question"],
                category=meta["category"],
                best_bid=meta["best_bid"],
                best_ask=meta["best_ask"],
                volume_usd=meta["volume"],
            )
            market_inputs[token_id] = (cs, meta["expiry"], meta["parseable"])

        ranked = rank_markets(market_inputs)
        print_watchlist(ranked, top_n=25)

    asyncio.run(_run())
