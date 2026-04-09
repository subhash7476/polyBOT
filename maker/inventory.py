"""InventoryManager + CircuitBreaker — tracks exposure, fires cancel-all."""

import asyncio
import time
from maker.state import MakerState
from maker.types import Fill, SkewUpdate, CancelAll
from market.state import AppState
from utils.logger import get_logger
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from maker.fill_ledger import FillLedger

log = get_logger(__name__)

COOLDOWN_SECONDS = 300         # 5 minutes
ADVERSE_COOLDOWN_SECONDS = 1800  # 30 minutes — adverse selection detected
RAPID_FILL_WINDOW = 5.0        # seconds
MAX_DAILY_LOSS_PCT = 0.03      # 3% of bankroll

# Adverse-selection detector: if >= ADVERSE_MIN_FILLS fills seen and
# >= ADVERSE_DIRECTION_PCT are the same side, trigger extended cooldown.
ADVERSE_MIN_FILLS = 5
ADVERSE_DIRECTION_PCT = 0.80


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

    async def handle_fill(self, fill: Fill) -> None:
        """Process a fill: update inventory, compute skew, check limits."""
        # 0. Snapshot market metadata and state in one lock acquisition
        question = ""
        end_date_iso = ""
        markets: dict = {}
        if self._app is not None:
            async with self._app._lock:
                markets = dict(self._app.markets)
                cs = markets.get(fill.token_id)
                if cs:
                    question = cs.question
                    end_date_iso = cs.end_date_iso

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

        # 2. Emit skew update
        skew = self._maker.skew_factor(fill.token_id)
        await self._skew_q.put(SkewUpdate(token_id=fill.token_id, skew_factor=skew))

        # 3. Forward to markout tracker (non-blocking; absent in tests)
        if self._markout_q is not None:
            await self._markout_q.put(fill)

        # 4. Check rapid double-fill
        await self._check_rapid_fill(fill)

        # 4b. Check adverse-selection (one-directional fills)
        await self._check_adverse_selection(fill)

        # 5. Check per-market inventory cap
        abs_pos = abs(self._maker.get_inventory(fill.token_id))
        if abs_pos >= self._maker.max_inventory_per_market:
            await self._cancel_q.put(CancelAll(fill.token_id))
            self._maker.cooldowns[fill.token_id] = time.time() + COOLDOWN_SECONDS
            log.warning(
                f"INVENTORY CAP: [{fill.token_id[:8]}] at ${abs_pos:.0f} "
                f"— quotes pulled for {COOLDOWN_SECONDS}s"
            )

        # 6. Check total inventory cap
        total = self._maker.total_abs_inventory
        if total >= self._maker.max_total_inventory:
            await self._cancel_q.put(CancelAll("*"))
            self._maker.global_cooldown_until = time.time() + COOLDOWN_SECONDS
            log.warning(
                f"TOTAL INVENTORY CAP: ${total:.0f} — ALL quotes pulled for {COOLDOWN_SECONDS}s"
            )

        # 7. Check daily loss — use MTM P&L so net-long inventory doesn't false-trigger
        if markets:
            mtm = self._maker.mtm_pnl(markets)
        else:
            mtm = self._maker.cash_pnl  # fallback if app_state not wired
        if mtm <= -self._max_daily_loss:
            await self._cancel_q.put(CancelAll("*"))
            log.warning(
                f"DAILY LOSS LIMIT: MTM=${mtm:.2f} — ALL quotes pulled"
            )

        log.info(
            f"INVENTORY: [{fill.token_id[:8]}] {fill.side} {fill.size:.2f} @ {fill.price:.3f} "
            f"→ net={self._maker.get_inventory(fill.token_id):.2f} "
            f"total_abs=${self._maker.total_abs_inventory:.0f} skew={skew:.2f}"
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

    async def run(self):
        """Main loop — process fills from queue."""
        while True:
            fill = await self._fills_q.get()
            try:
                await self.handle_fill(fill)
            except Exception as exc:
                log.exception(f"InventoryManager error: {exc}")
