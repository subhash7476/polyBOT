"""MarketSelector actor — picks which sports markets to quote."""

import asyncio
from collections import OrderedDict
from market.state import AppState, ContractState
from utils.logger import get_logger

log = get_logger(__name__)

_MIN_DAILY_VOLUME = 100.0
_MAX_ACTIVE_MARKETS = 20
_MIN_SPREAD = 0.04
_TARGET_CATEGORY = "sports"


class MarketSelector:
    """Selects sports markets worth quoting."""

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
        """Filter to quotable sports markets, rank by spread * volume."""
        candidates = []
        for token_id, cs in markets.items():
            if cs.category != _TARGET_CATEGORY:
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
            async with self._state._lock:
                markets = dict(self._state.markets)

            selected = self.filter_and_rank(markets)
            token_ids = set(selected.keys())

            log.info(
                f"MarketSelector: {len(token_ids)} sports markets selected "
                f"(from {len(markets)} total)"
            )

            await self._active_markets_q.put(token_ids)
            await asyncio.sleep(self.REFRESH_INTERVAL)
