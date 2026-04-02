"""QuoteEngine actor — computes fair value and bid/ask quotes."""

import asyncio
import time
from maker.state import MakerState
from maker.types import QuoteIntent, SkewUpdate, LadderUpdate
from market.state import AppState
from utils.logger import get_logger

log = get_logger(__name__)

BASE_SPREAD = 0.06
MIN_SPREAD = 0.04
MAX_SPREAD = 0.15
QUOTE_SIZE_USDC = 10.0
MAX_SKEW_ADJ = 0.03
LADDER_LEVELS = 3    # bid+ask pairs per market
LEVEL_STEP = 0.01    # price offset between ladder levels


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
    FORCE_REPRICE_INTERVAL = 30.0  # send quotes for all active markets every 30s

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
        self._last_force_reprice: float = 0.0

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
    def build_ladder(
        token_id: str,
        fair_value: float,
        spread: float,
        size: float,
        reason: str,
    ) -> LadderUpdate:
        """Build a LADDER_LEVELS-deep ladder centred on fair_value.

        Level layout (LADDER_LEVELS=3, center index=1):
          index 0: bid = fv - half - LEVEL_STEP,  ask = fv + half + LEVEL_STEP
          index 1: bid = fv - half,               ask = fv + half          ← center
          index 2: bid = fv - half + LEVEL_STEP,  ask = fv + half - LEVEL_STEP

        Tightest level (index 2) is closest to mid. Widest (index 0) is outermost.
        All levels carry equal size.
        """
        half = spread / 2.0
        center_idx = LADDER_LEVELS // 2
        levels = []
        for i in range(LADDER_LEVELS):
            offset = (center_idx - i) * LEVEL_STEP
            bid = round(_clamp(fair_value - half - offset, 0.01, 0.99), 4)
            ask = round(_clamp(fair_value + half + offset, 0.01, 0.99), 4)
            if bid >= ask:
                continue  # skip degenerate level (very near 0 or 1)
            levels.append(QuoteIntent(
                token_id=token_id,
                bid_price=bid,
                ask_price=ask,
                bid_size=size,
                ask_size=size,
                reason=reason,
            ))
        return LadderUpdate(token_id=token_id, levels=levels, reason=reason)

    @staticmethod
    def is_stale(old: QuoteIntent, new: QuoteIntent, tick: float = 0.01) -> bool:
        """True if the new quote differs enough from the old to warrant a reprice."""
        return (
            abs(old.bid_price - new.bid_price) >= tick
            or abs(old.ask_price - new.ask_price) >= tick
        )

    async def run(self):
        while True:
            now = time.monotonic()
            force = (now - self._last_force_reprice) >= self.FORCE_REPRICE_INTERVAL

            # Drain market selector updates (non-blocking)
            prev_ids = self._active_token_ids
            while not self._active_markets_q.empty():
                try:
                    self._active_token_ids = self._active_markets_q.get_nowait()
                except asyncio.QueueEmpty:
                    break
            new_ids = self._active_token_ids - prev_ids

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

                ladder = self.build_ladder(
                    token_id=token_id,
                    fair_value=fv,
                    spread=spread,
                    size=QUOTE_SIZE_USDC,
                    reason="reprice",
                )
                old_center = self._maker.last_quotes.get(token_id)
                is_new = token_id in new_ids or old_center is None
                if is_new or force or self.is_stale(old_center, ladder.center):
                    self._maker.last_quotes[token_id] = ladder.center
                    await self._quote_intents_q.put(ladder)
                    log.debug(
                        f"ladder [{token_id[:8]}] levels={len(ladder.levels)} "
                        f"center={ladder.center.bid_price:.3f}/{ladder.center.ask_price:.3f} "
                        f"force={force} new={is_new}"
                    )

            if force:
                self._last_force_reprice = now

            await asyncio.sleep(self.REPRICE_INTERVAL)
