"""MarketSelector actor — picks which markets to quote."""

import asyncio
import httpx
from collections import OrderedDict
from market.state import AppState, ContractState
from market.clob_monitor import fetch_active_markets
from utils.logger import get_logger

log = get_logger(__name__)

_MIN_DAILY_VOLUME = 1_000.0
_MAX_ACTIVE_MARKETS = 20
_MIN_SPREAD = 0.04
_TARGET_CATEGORIES = frozenset({"sports", "event"})


class MarketSelector:
    """Selects quotable markets (sports + event) ranked by spread * volume."""

    REFRESH_INTERVAL = 900  # 15 minutes

    def __init__(
        self,
        state: AppState,
        active_markets_q: asyncio.Queue,
    ):
        self._state = state
        self._active_markets_q = active_markets_q

    @staticmethod
    def filter_and_rank(
        markets: dict[str, ContractState],
        max_markets: int = _MAX_ACTIVE_MARKETS,
    ) -> OrderedDict[str, ContractState]:
        """Filter to quotable markets, rank by spread * volume."""
        candidates = []
        for token_id, cs in markets.items():
            if cs.category not in _TARGET_CATEGORIES:
                continue
            spread = cs.best_ask - cs.best_bid
            if spread < _MIN_SPREAD:
                continue
            if cs.volume_usd < _MIN_DAILY_VOLUME:
                continue
            score = spread * cs.volume_usd
            candidates.append((token_id, cs, score))

        candidates.sort(key=lambda x: -x[2])
        result = OrderedDict()
        for token_id, cs, _ in candidates[:max_markets]:
            result[token_id] = cs
        return result

    async def _discover_and_seed(self) -> None:
        """
        Fetch all markets from Gamma, find wide-spread sports+event markets,
        and seed any missing ones into app_state.markets.

        The CLOBMonitor's 250-slot selection is sorted by volume (taker-optimised),
        which crowds out wide-spread niche markets. This method ensures the maker
        bot has the right markets regardless of CLOBMonitor selection order.
        """
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                token_map = await fetch_active_markets(client)
        except Exception as exc:
            log.warning(f"MarketSelector Gamma fetch failed: {exc}")
            return

        candidates = []
        for yes_id, meta in token_map.items():
            if meta["category"] not in _TARGET_CATEGORIES:
                continue
            spread = meta["best_ask"] - meta["best_bid"]
            if spread < _MIN_SPREAD:
                continue
            if meta.get("volume", 0) < _MIN_DAILY_VOLUME:
                continue
            candidates.append((yes_id, meta, spread * meta.get("volume", 0)))

        candidates.sort(key=lambda x: -x[2])
        top = candidates[:_MAX_ACTIVE_MARKETS * 2]  # seed 2x buffer

        seeded = 0
        async with self._state._lock:
            existing = set(self._state.markets.keys())
            for yes_id, meta, _ in top:
                if yes_id in existing:
                    continue
                cs = ContractState(
                    yes_token_id=yes_id,
                    no_token_id=meta.get("no_token_id", ""),
                    question=meta["question"],
                    category=meta["category"],
                    best_bid=meta["best_bid"],
                    best_ask=meta["best_ask"],
                    volume_usd=meta.get("volume", 0),
                    condition_id=meta.get("condition_id", ""),
                )
                self._state.markets[yes_id] = cs
                seeded += 1

        if seeded:
            log.info(f"MarketSelector seeded {seeded} wide-spread markets into state")

    async def run(self):
        """Main loop — re-evaluate market selection every REFRESH_INTERVAL."""
        # Wait for CLOBMonitor to seed initial markets before first selection
        while True:
            async with self._state._lock:
                n = len(self._state.markets)
            if n > 0:
                break
            await asyncio.sleep(2.0)

        while True:
            # Top-up state with wide-spread markets the CLOBMonitor may have missed
            await self._discover_and_seed()

            async with self._state._lock:
                markets = dict(self._state.markets)

            selected = self.filter_and_rank(markets)
            by_cat: dict[str, int] = {}
            for cs in selected.values():
                by_cat[cs.category] = by_cat.get(cs.category, 0) + 1

            log.info(
                f"MarketSelector: {len(selected)} markets selected "
                f"(from {len(markets)} total) — {by_cat}"
            )

            await self._active_markets_q.put(set(selected.keys()))
            await asyncio.sleep(self.REFRESH_INTERVAL)
