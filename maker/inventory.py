"""InventoryManager + CircuitBreaker — tracks exposure, fires cancel-all."""

import asyncio
import time
from datetime import datetime, timezone
from maker.state import MakerState
from maker.types import Fill, SkewUpdate, CancelAll
from market.state import AppState
from utils.logger import get_logger
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from maker.fill_ledger import FillLedger

from config import (
    MAKER_COOLDOWN_SECONDS, MAKER_ADVERSE_COOLDOWN_SECONDS,
    MAKER_RAPID_FILL_WINDOW, MAKER_MAX_DAILY_LOSS_PCT,
    MAKER_ADVERSE_MIN_FILLS, MAKER_ADVERSE_DIRECTION_PCT,
)

log = get_logger(__name__)

COOLDOWN_SECONDS           = MAKER_COOLDOWN_SECONDS
ADVERSE_COOLDOWN_SECONDS   = MAKER_ADVERSE_COOLDOWN_SECONDS
RAPID_FILL_WINDOW          = MAKER_RAPID_FILL_WINDOW
MAX_DAILY_LOSS_PCT         = MAKER_MAX_DAILY_LOSS_PCT
ABSENT_MARKET_EXPIRY_HOURS = 4.0
CAP_HIT_WINDOW_SECONDS     = 3600
MAX_CAP_COOLDOWN_SECONDS   = 3600

# Hysteresis: after global cooldown expires, re-engage if inventory still above this
# fraction of max_total_inventory. Prevents the ratchet where expiry → fill → cap fires.
TOTAL_INV_RESUME_RATIO = 0.80

ADVERSE_MIN_FILLS     = MAKER_ADVERSE_MIN_FILLS
ADVERSE_DIRECTION_PCT = MAKER_ADVERSE_DIRECTION_PCT


class InventoryManager:
    """Tracks per-market inventory, computes skew, fires circuit breakers."""

    def __init__(
        self,
        maker_state: MakerState,
        fills_q: asyncio.Queue,
        skew_updates_q: asyncio.Queue,
        cancel_q: asyncio.Queue,
        bankroll: float = 500.0,
        app_state: AppState | None = None,
        markout_q: asyncio.Queue | None = None,
        fill_ledger: "FillLedger | None" = None,
    ):
        self._maker = maker_state
        self._app = app_state
        self._fills_q = fills_q
        self._skew_q = skew_updates_q
        self._cancel_q = cancel_q
        self._markout_q = markout_q
        self._fill_ledger = fill_ledger
        self._max_daily_loss = bankroll * MAX_DAILY_LOSS_PCT

    def _inventory_cap_cooldown_seconds(self, token_id: str) -> int:
        """Escalate cooldowns when the same market repeatedly hits its cap."""
        now = time.time()
        hit = self._maker.inventory_cap_hits.get(token_id, {})
        last_hit = float(hit.get("last_hit", 0.0) or 0.0)
        count = int(hit.get("count", 0) or 0)
        count = count + 1 if now - last_hit <= CAP_HIT_WINDOW_SECONDS else 1
        self._maker.inventory_cap_hits[token_id] = {"count": count, "last_hit": now}

        cooldown = COOLDOWN_SECONDS * (2 ** (count - 1))
        return int(min(cooldown, MAX_CAP_COOLDOWN_SECONDS))

    async def handle_fill(self, fill: Fill) -> None:
        """Process a fill: update inventory, compute skew, check limits."""
        # 0. Snapshot market metadata and state in one lock acquisition
        question = ""
        end_date_iso = ""
        category = ""
        markets: dict = {}
        if self._app is not None:
            async with self._app._lock:
                markets = dict(self._app.markets)
                cs = markets.get(fill.token_id)
                if cs:
                    question = cs.question
                    end_date_iso = cs.end_date_iso
                    category = cs.category

        # 1. Update inventory and record fill (capture realized delta for ledger)
        _realized_before = self._maker.realized_pnl
        self._maker.update_inventory(fill.token_id, fill.side, fill.size)
        self._maker.record_fill(fill.token_id, fill.side, fill.price, fill.size, fill.filled_at,
                                question=question, end_date_iso=end_date_iso)

        # 1b. Persist to fill ledger; track fill_id so replay skips it on restart
        if self._fill_ledger is not None:
            cash_flow = (fill.price * fill.size) if fill.side == "SELL" else -(fill.price * fill.size)
            fill_id = self._fill_ledger.append(
                session_id=self._maker.session_id,
                token_id=fill.token_id,
                question=question,
                side=fill.side,
                price=fill.price,
                size=fill.size,
                cash_flow=cash_flow,
                realized_pnl=self._maker.realized_pnl - _realized_before,
                inventory_after=self._maker.get_inventory(fill.token_id),
                filled_at=fill.filled_at,
            )
            self._maker.daily_fills_seen.add(fill_id)

        # 2. Emit skew update — pass category so weather uses 5-share cap as denominator
        skew = self._maker.skew_factor(fill.token_id, category)
        await self._skew_q.put(SkewUpdate(token_id=fill.token_id, skew_factor=skew))

        # 3. Forward to markout tracker (non-blocking; absent in tests)
        if self._markout_q is not None:
            await self._markout_q.put(fill)

        # 4. Check rapid double-fill
        await self._check_rapid_fill(fill)

        # 4b. Check adverse-selection (one-directional fills)
        await self._check_adverse_selection(fill)

        # 5. Check per-market inventory cap (category-specific if configured)
        abs_pos = abs(self._maker.get_inventory(fill.token_id))
        mkt_cap = self._maker.max_inventory_for_category(category)
        if abs_pos >= mkt_cap:
            await self._cancel_q.put(CancelAll(fill.token_id))
            cooldown_seconds = self._inventory_cap_cooldown_seconds(fill.token_id)
            self._maker.cooldowns[fill.token_id] = time.time() + cooldown_seconds
            log.warning(
                f"INVENTORY CAP [{category or 'unknown'}]: [{fill.token_id[:8]}] at {abs_pos:.0f} shares"
                f" (cap={mkt_cap:.0f}) — quotes pulled for {cooldown_seconds}s"
            )
            self._promote_over_cap_to_reduce_only()

        # 6. Check total inventory cap
        total = self._maker.total_abs_inventory
        if total >= self._maker.max_total_inventory:
            await self._cancel_q.put(CancelAll("*"))
            self._maker.global_cooldown_until = time.time() + COOLDOWN_SECONDS
            log.warning(
                f"TOTAL INVENTORY CAP: {total:.0f} shares — ALL quotes pulled for {COOLDOWN_SECONDS}s"
            )

        # 7. Check daily loss — use MTM P&L so net-long inventory doesn't false-trigger
        if markets:
            mtm = self._maker.mtm_pnl(markets)
        else:
            mtm = self._maker.cash_pnl  # fallback if app_state not wired
        if mtm <= -self._max_daily_loss:
            await self._cancel_q.put(CancelAll("*"))
            self._maker.global_cooldown_until = time.time() + ADVERSE_COOLDOWN_SECONDS
            log.warning(
                f"DAILY LOSS LIMIT: MTM=${mtm:.2f} — ALL quotes pulled for {ADVERSE_COOLDOWN_SECONDS}s"
            )

        log.info(
            f"INVENTORY: [{fill.token_id[:8]}] {fill.side} {fill.size:.2f} @ {fill.price:.3f} "
            f"→ net={self._maker.get_inventory(fill.token_id):.2f} "
            f"total_abs={self._maker.total_abs_inventory:.0f} shares skew={skew:.2f}"
        )

    async def _check_rapid_fill(self, fill: Fill) -> None:
        """Detect both sides filled within RAPID_FILL_WINDOW."""
        times = self._maker.last_fill_times.setdefault(fill.token_id, {})
        other_side = "SELL" if fill.side == "BUY" else "BUY"

        if other_side in times and (fill.filled_at - times[other_side]) < RAPID_FILL_WINDOW:
            await self._cancel_q.put(CancelAll(fill.token_id))
            self._maker.cooldowns[fill.token_id] = time.time() + COOLDOWN_SECONDS
            log.warning(
                f"RAPID DOUBLE-FILL: [{fill.token_id[:8]}] both sides hit within "
                f"{RAPID_FILL_WINDOW}s — quotes pulled"
            )

        times[fill.side] = fill.filled_at

    async def _check_adverse_selection(self, fill: Fill) -> None:
        """Detect one-directional fill streams indicating adverse selection.

        If >= ADVERSE_MIN_FILLS fills have been seen on a market and
        >= ADVERSE_DIRECTION_PCT are the same side, the bot is being consistently
        picked off. Trigger extended cooldown and warn.
        """
        sides = self._maker.recent_fill_sides.setdefault(fill.token_id, [])
        sides.append(fill.side)
        # Keep only the last 20 fills per market
        if len(sides) > 20:
            sides[:] = sides[-20:]

        n = len(sides)
        if n < ADVERSE_MIN_FILLS:
            return

        buy_count = sides.count("BUY")
        sell_count = n - buy_count
        dominant = max(buy_count, sell_count)
        if dominant / n >= ADVERSE_DIRECTION_PCT:
            dominant_side = "BUY" if buy_count > sell_count else "SELL"
            await self._cancel_q.put(CancelAll(fill.token_id))
            self._maker.cooldowns[fill.token_id] = time.time() + ADVERSE_COOLDOWN_SECONDS
            # Reset so it doesn't keep re-triggering every fill
            self._maker.recent_fill_sides[fill.token_id] = []
            log.warning(
                f"ADVERSE SELECTION: [{fill.token_id[:8]}] {dominant}/{n} fills are "
                f"{dominant_side} — quotes pulled for {ADVERSE_COOLDOWN_SECONDS}s (30 min)"
            )

    def _promote_over_cap_to_reduce_only(self) -> None:
        """Add over-cap positions to reduce_only_markets so quote_engine quotes the closing side.

        Runs each cleanup cycle so recently-lowered cap limits are enforced even on
        positions that predate the cap change.  Tokens are removed once drained below
        cap — but only if they are still selected (orphaned tokens are managed by
        MarketSelector which will remove them when inventory reaches zero).
        """
        if self._maker.max_inventory_per_market <= 0:
            return

        # Snapshot categories — safe because this sync method runs atomically within
        # the asyncio event loop tick; no other coroutine can mutate app.markets here.
        categories: dict[str, str] = (
            {tid: cs.category for tid, cs in self._app.markets.items()}
            if self._app is not None else {}
        )

        added: list[str] = []
        removed: list[str] = []
        for token_id, inv in list(self._maker.inventory.items()):
            if inv == 0.0:
                continue
            category = categories.get(token_id, "")
            cap = self._maker.max_inventory_for_category(category)
            over_cap = abs(inv) >= cap
            in_reduce = token_id in self._maker.reduce_only_markets
            if over_cap and not in_reduce:
                self._maker.reduce_only_markets.add(token_id)
                added.append(token_id)
            elif not over_cap and in_reduce and token_id in self._maker.selected_token_ids:
                # Only remove if still selected — MarketSelector manages orphaned tokens.
                self._maker.reduce_only_markets.discard(token_id)
                removed.append(token_id)
        if added:
            log.info(f"Over-cap: promoted {len(added)} token(s) to reduce_only — "
                     f"{', '.join(t[:8] for t in added[:5])}")
        if removed:
            log.info(f"Over-cap: {len(removed)} token(s) drained below cap — removed from reduce_only")

    async def _engage_cooldown_if_above_threshold(self) -> None:
        """Re-engage global cooldown if inventory is still above the resume threshold.

        Called from cleanup_loop after position expiry. Prevents the ratchet where
        the 300-s cooldown expires, quotes resume, a fill arrives, and the cap fires
        again — repeating indefinitely without draining inventory.
        """
        total = self._maker.total_abs_inventory
        resume_threshold = self._maker.max_total_inventory * TOTAL_INV_RESUME_RATIO
        if total > resume_threshold and not self._maker.global_in_cooldown():
            self._maker.global_cooldown_until = time.time() + COOLDOWN_SECONDS
            log.warning(
                f"INVENTORY RESUME BLOCKED: {total:.0f} > {resume_threshold:.0f} "
                f"({TOTAL_INV_RESUME_RATIO * 100:.0f}% of {self._maker.max_total_inventory:.0f}) — "
                f"extending global cooldown {COOLDOWN_SECONDS}s"
            )

    async def _expire_paper_positions(self) -> int:
        """Zero out inventory for markets whose resolution date has passed.

        In paper/shadow mode positions never settle via on-chain redemption, so
        resolved markets accumulate in self._maker.inventory indefinitely.  This
        inflates total_abs_inventory, which causes the global inventory cap to
        fire on every fill — generating a cancel-all storm and starving active
        markets of quotes.

        We do NOT book synthetic P&L for expired positions: the paper fills were
        already recorded in the fill ledger and any realized P&L was computed at
        fill time.  We simply zero the leftover inventory entry so it no longer
        distorts total_abs_inventory.

        Returns the number of markets whose inventory was zeroed.
        """
        if self._app is None:
            return 0

        now = time.time()

        # Snapshot markets under lock so we read a consistent view.
        async with self._app._lock:
            markets = dict(self._app.markets)

        expired_count = 0
        for token_id, inv in list(self._maker.inventory.items()):
            if inv == 0.0:
                continue  # already zeroed — skip

            cs = markets.get(token_id)
            if cs is None:
                # Market absent from app state — could be "not yet selected" or "resolved".
                # Use inventory_entry_time to distinguish: if we've held a position for
                # longer than ABSENT_MARKET_EXPIRY_HOURS without the market reappearing,
                # treat it as resolved and zero the stale inventory.
                # Default to epoch (0) so positions with no tracked entry time
                # are treated as old — happens after first restart with old checkpoint.
                entry = self._maker.inventory_entry_time.get(token_id, 0.0)
                age_hours = (now - entry) / 3600
                if age_hours < ABSENT_MARKET_EXPIRY_HOURS:
                    continue  # too recent — may just not be selected yet
                old_inv = self._maker.inventory[token_id]
                self._maker.inventory[token_id] = 0.0
                self._maker.inventory_entry_time.pop(token_id, None)
                expired_count += 1
                log.warning(
                    f"ABSENT MARKET EXPIRY: [{token_id[:8]}] {old_inv:+.2f} shares zeroed "
                    f"(absent from state for {age_hours:.1f}h)"
                )
                continue

            if not cs.end_date_iso:
                continue  # no end date recorded, cannot determine expiry

            try:
                end_dt = datetime.fromisoformat(cs.end_date_iso.replace("Z", "+00:00"))
            except ValueError:
                continue  # malformed date string, skip

            if end_dt.timestamp() > now:
                continue  # market still active

            # Market has passed its resolution date — zero the stale position.
            old_inv = self._maker.inventory[token_id]
            self._maker.inventory[token_id] = 0.0
            expired_count += 1
            log.warning(
                f"EXPIRED POSITION: [{token_id[:8]}] {old_inv:+.2f} shares zeroed "
                f"(market ended {cs.end_date_iso})"
            )

        return expired_count

    async def cleanup_loop(self) -> None:
        """Periodic cleanup of stale paper positions.

        The first run is delayed 60 s so CLOBMonitor has time to seed
        app_state.markets before we inspect end dates.  Subsequent runs
        happen every 5 minutes — frequent enough to prevent total_abs_inventory
        from staying inflated across session boundaries.
        """
        await asyncio.sleep(60)  # wait for CLOBMonitor to populate markets
        while True:
            try:
                expired = await self._expire_paper_positions()
                if expired:
                    log.info(
                        f"Expired {expired} stale paper position(s); "
                        f"total_abs_inventory now {self._maker.total_abs_inventory:.0f} shares"
                    )
                self._promote_over_cap_to_reduce_only()
                await self._engage_cooldown_if_above_threshold()
            except Exception as exc:
                log.exception(f"cleanup_loop error: {exc}")
            await asyncio.sleep(300)  # re-check every 5 minutes

    async def run(self):
        """Main loop — process fills from queue."""
        while True:
            fill = await self._fills_q.get()
            try:
                await self.handle_fill(fill)
            except Exception as exc:
                log.exception(f"InventoryManager error: {exc}")
