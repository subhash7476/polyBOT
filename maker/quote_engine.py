"""QuoteEngine actor — computes fair value and bid/ask quotes."""

import asyncio
import time
from datetime import datetime, timezone
from maker.state import MakerState
from maker.types import QuoteIntent, SkewUpdate, LadderUpdate
from market.state import AppState
from engine.falcon_signals import compute_adverse_selection_penalty, compute_market_skew_adjustment
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
    """Dynamic spread based on volume, inventory, and time to resolution.

    Spread schedule (we only quote 4h–7d markets):
      < 4h : MAX_SPREAD — imminent resolution, stop quoting (MarketSelector excludes these)
      < 12h: +0.06 — very high uncertainty / adverse selection risk
      < 24h: +0.04 — elevated risk
      < 72h: +0.02 — moderate
      3–7d : BASE_SPREAD — normal window
    """
    spread = BASE_SPREAD

    if volume_usd < 500.0:
        spread += 0.02

    spread += abs_inventory * 0.01

    if hours_to_expiry < 4.0:
        return MAX_SPREAD
    elif hours_to_expiry < 12.0:
        spread += 0.06
    elif hours_to_expiry < 24.0:
        spread += 0.04
    elif hours_to_expiry < 72.0:
        spread += 0.02

    return max(MIN_SPREAD, min(spread, MAX_SPREAD))


class QuoteEngine:
    """Computes quotes for active markets, event-driven on price ticks."""

    FORCE_REPRICE_INTERVAL = 30.0  # fallback: reprice all markets if no tick arrives
    CLOB_STALE_SECONDS = 30.0      # pull all quotes if CLOB feed silent this long

    def __init__(
        self,
        app_state: AppState,
        maker_state: MakerState,
        active_markets_q: asyncio.Queue,
        quote_intents_q: asyncio.Queue,
        skew_updates_q: asyncio.Queue,
        price_update_q: asyncio.Queue | None = None,
        cancel_q: asyncio.Queue | None = None,
    ):
        self._app = app_state
        self._maker = maker_state
        self._active_markets_q = active_markets_q
        self._quote_intents_q = quote_intents_q
        self._skew_updates_q = skew_updates_q
        self._price_update_q = price_update_q
        self._cancel_q = cancel_q
        self._clob_stale = False  # tracks whether we already pulled quotes
        self._active_token_ids: set[str] = set()
        self._last_force_reprice: float = 0.0

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

    async def _reprice(self, tokens_to_check: set[str], force: bool, new_ids: set[str]) -> None:
        """Compute and emit ladder updates for the given token set."""
        async with self._app._lock:
            markets = dict(self._app.markets)
            feeds = self._app.feeds  # reference — Falcon lookups only read, never mutate

        for token_id in tokens_to_check:
            cs = markets.get(token_id)
            if not cs:
                continue
            if self._maker.in_cooldown(token_id):
                continue

            skew = self._maker.skew_factor(token_id)
            abs_inv = abs(self._maker.get_inventory(token_id))

            # Per-market Falcon adjustments (condition_id matches Market Insights data)
            spread_multiplier = compute_adverse_selection_penalty(cs.condition_id, feeds)
            model_adj = compute_market_skew_adjustment(cs.condition_id, feeds)

            # Compute hours to expiry from market end date
            hours_to_expiry = 999.0
            if cs.end_date_iso:
                try:
                    end_dt = datetime.fromisoformat(cs.end_date_iso.replace("Z", "+00:00"))
                    hours_to_expiry = max(0.0, (end_dt.timestamp() - time.time()) / 3600.0)
                except ValueError:
                    pass

            fv = compute_fair_value(mid=cs.mid, skew=skew, model_adj=model_adj)
            base_spread = compute_spread(
                volume_usd=cs.volume_usd,
                abs_inventory=abs_inv,
                hours_to_expiry=hours_to_expiry,
            )
            spread = min(base_spread * spread_multiplier, MAX_SPREAD)

            # Book-relative quoting: compress toward book spread but never below
            # MIN_SPREAD — we need at least that much to capture edge after fees.
            # If the book is tighter than MIN_SPREAD, we quote at MIN_SPREAD
            # (outside the book is fine — we wait for the book to come to us).
            book_spread = cs.best_ask - cs.best_bid
            floor = MIN_SPREAD
            if book_spread > 0 and spread > book_spread:
                eff_spread = max(book_spread, floor)
                eff_half = eff_spread / 2.0
                eff_fv = _clamp(fv, cs.best_bid + eff_half, cs.best_ask - eff_half)
            else:
                eff_fv = fv
                eff_spread = spread

            ladder = self.build_ladder(
                token_id=token_id,
                fair_value=eff_fv,
                spread=eff_spread,
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

    async def run(self):
        while True:
            # --- Wait for a price tick or force-reprice timeout ---
            triggered_ids: set[str] = set()
            force = False

            if self._price_update_q is not None:
                # Event-driven: block until a tick arrives or 30s elapses
                time_since_force = time.monotonic() - self._last_force_reprice
                timeout = max(0.1, self.FORCE_REPRICE_INTERVAL - time_since_force)
                try:
                    token_id = await asyncio.wait_for(
                        self._price_update_q.get(), timeout=timeout
                    )
                    triggered_ids.add(token_id)
                    # Drain any additional ticks that arrived while we were processing
                    while not self._price_update_q.empty():
                        try:
                            triggered_ids.add(self._price_update_q.get_nowait())
                        except asyncio.QueueEmpty:
                            break
                except asyncio.TimeoutError:
                    force = True  # no tick arrived — force-reprice everything
            else:
                # Fallback polling mode (no price_update_q wired)
                await asyncio.sleep(1.5)

            # --- Drain market selector and skew updates (non-blocking) ---
            prev_ids = self._active_token_ids
            while not self._active_markets_q.empty():
                try:
                    self._active_token_ids = self._active_markets_q.get_nowait()
                except asyncio.QueueEmpty:
                    break
            new_ids = self._active_token_ids - prev_ids

            while not self._skew_updates_q.empty():
                try:
                    self._skew_updates_q.get_nowait()
                except asyncio.QueueEmpty:
                    break

            # Check force-reprice regardless of path
            now = time.monotonic()
            if (now - self._last_force_reprice) >= self.FORCE_REPRICE_INTERVAL:
                force = True

            # Decide which markets to reprice
            if force or new_ids:
                tokens_to_check = self._active_token_ids
            else:
                # Only reprice the ticked markets that we're actively quoting
                tokens_to_check = triggered_ids & self._active_token_ids

            # CLOB staleness circuit breaker: if the feed has gone silent, pull all
            # quotes and wait. Quoting on stale prices is worse than not quoting.
            clob_age = time.time() - self._app.feeds.last_feed_update.get("clob", 0)
            if clob_age > self.CLOB_STALE_SECONDS:
                if not self._clob_stale:
                    self._clob_stale = True
                    log.warning(
                        f"CLOB feed stale ({clob_age:.0f}s) — pulling all quotes"
                    )
                    if self._cancel_q is not None:
                        from maker.types import CancelAll
                        await self._cancel_q.put(CancelAll("*"))
                continue  # skip reprice until feed recovers

            if self._clob_stale:
                log.info("CLOB feed recovered — resuming quotes")
                self._clob_stale = False

            if tokens_to_check:
                if self._maker.global_in_cooldown():
                    log.debug("global inventory cooldown active — skipping reprice")
                else:
                    await self._reprice(tokens_to_check, force=force or bool(new_ids), new_ids=new_ids)

            if force:
                self._last_force_reprice = now
