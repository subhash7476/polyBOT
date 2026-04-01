"""QuoteEngine actor — computes fair value and bid/ask quotes."""

import asyncio
from maker.state import MakerState
from maker.types import QuoteIntent, SkewUpdate
from market.state import AppState
from utils.logger import get_logger

log = get_logger(__name__)

BASE_SPREAD = 0.06
MIN_SPREAD = 0.04
MAX_SPREAD = 0.15
QUOTE_SIZE_USDC = 10.0
MAX_SKEW_ADJ = 0.03


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def compute_fair_value(mid: float, skew: float, model_adj: float) -> float:
    """
    Hybrid fair value: market mid + inventory skew + model adjustment.
    skew: [-1, +1] from MakerState.skew_factor(). Positive = holding YES.
    model_adj: 0.0 until Bayesian calibration is ready (Phase 2+).
    """
    inventory_adj = skew * MAX_SKEW_ADJ
    return _clamp(mid + inventory_adj + model_adj, 0.05, 0.95)


def compute_spread(
    volume_usd: float,
    abs_inventory: float,
    hours_to_expiry: float,
) -> float:
    """Dynamic spread based on volume, inventory, and time to resolution."""
    spread = BASE_SPREAD

    if volume_usd < 500.0:
        spread += 0.02

    spread += abs_inventory * 0.01

    if hours_to_expiry < 6.0:
        return MAX_SPREAD
    if hours_to_expiry < 48.0:
        spread += 0.03

    return max(MIN_SPREAD, spread)


class QuoteEngine:
    """Computes quotes for active markets on a 1.5s cycle."""

    REPRICE_INTERVAL = 1.5

    def __init__(
        self,
        app_state: AppState,
        maker_state: MakerState,
        active_markets_q: asyncio.Queue,
        quote_intents_q: asyncio.Queue,
        skew_updates_q: asyncio.Queue,
    ):
        self._app = app_state
        self._maker = maker_state
        self._active_markets_q = active_markets_q
        self._quote_intents_q = quote_intents_q
        self._skew_updates_q = skew_updates_q
        self._active_token_ids: set[str] = set()

    @staticmethod
    def build_quote(
        token_id: str,
        fair_value: float,
        spread: float,
        bid_size: float,
        ask_size: float,
        reason: str,
    ) -> QuoteIntent:
        half = spread / 2.0
        return QuoteIntent(
            token_id=token_id,
            bid_price=round(fair_value - half, 4),
            ask_price=round(fair_value + half, 4),
            bid_size=bid_size,
            ask_size=ask_size,
            reason=reason,
        )

    @staticmethod
    def is_stale(old: QuoteIntent, new: QuoteIntent, tick: float = 0.01) -> bool:
        """True if the new quote differs enough from the old to warrant a reprice."""
        return (
            abs(old.bid_price - new.bid_price) >= tick
            or abs(old.ask_price - new.ask_price) >= tick
        )

    async def run(self):
        while True:
            # Drain market selector updates (non-blocking)
            while not self._active_markets_q.empty():
                try:
                    self._active_token_ids = self._active_markets_q.get_nowait()
                except asyncio.QueueEmpty:
                    break

            # Drain skew updates (non-blocking)
            while not self._skew_updates_q.empty():
                try:
                    self._skew_updates_q.get_nowait()
                except asyncio.QueueEmpty:
                    break

            async with self._app._lock:
                markets = dict(self._app.markets)

            for token_id in self._active_token_ids:
                cs = markets.get(token_id)
                if not cs:
                    continue
                if self._maker.in_cooldown(token_id):
                    continue

                skew = self._maker.skew_factor(token_id)
                abs_inv = abs(self._maker.get_inventory(token_id))

                fv = compute_fair_value(mid=cs.mid, skew=skew, model_adj=0.0)
                spread = compute_spread(
                    volume_usd=cs.volume_usd,
                    abs_inventory=abs_inv,
                    hours_to_expiry=999.0,  # TODO: compute from ContractState expiry
                )

                new_quote = self.build_quote(
                    token_id=token_id,
                    fair_value=fv,
                    spread=spread,
                    bid_size=QUOTE_SIZE_USDC,
                    ask_size=QUOTE_SIZE_USDC,
                    reason="reprice",
                )

                old_quote = self._maker.last_quotes.get(token_id)
                if old_quote is None or self.is_stale(old_quote, new_quote):
                    self._maker.last_quotes[token_id] = new_quote
                    await self._quote_intents_q.put(new_quote)

            await asyncio.sleep(self.REPRICE_INTERVAL)
