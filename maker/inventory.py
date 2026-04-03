"""InventoryManager + CircuitBreaker — tracks exposure, fires cancel-all."""

import asyncio
import time
from maker.state import MakerState
from maker.types import Fill, SkewUpdate, CancelAll
from market.state import AppState
from utils.logger import get_logger

log = get_logger(__name__)

COOLDOWN_SECONDS = 300  # 5 minutes
RAPID_FILL_WINDOW = 5.0  # seconds
MAX_DAILY_LOSS_PCT = 0.03  # 3% of bankroll


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
    ):
        self._maker = maker_state
        self._app = app_state
        self._fills_q = fills_q
        self._skew_q = skew_updates_q
        self._cancel_q = cancel_q
        self._max_daily_loss = bankroll * MAX_DAILY_LOSS_PCT

    async def handle_fill(self, fill: Fill) -> None:
        """Process a fill: update inventory, compute skew, check limits."""
        # 1. Update inventory and record fill
        self._maker.update_inventory(fill.token_id, fill.side, fill.size)
        self._maker.record_fill(fill.token_id, fill.side, fill.price, fill.size, fill.filled_at)

        # 2. Emit skew update
        skew = self._maker.skew_factor(fill.token_id)
        await self._skew_q.put(SkewUpdate(token_id=fill.token_id, skew_factor=skew))

        # 3. Check rapid double-fill
        await self._check_rapid_fill(fill)

        # 4. Check per-market inventory cap
        abs_pos = abs(self._maker.get_inventory(fill.token_id))
        if abs_pos >= self._maker.max_inventory_per_market:
            await self._cancel_q.put(CancelAll(fill.token_id))
            self._maker.cooldowns[fill.token_id] = time.time() + COOLDOWN_SECONDS
            log.warning(
                f"INVENTORY CAP: [{fill.token_id[:8]}] at ${abs_pos:.0f} "
                f"— quotes pulled for {COOLDOWN_SECONDS}s"
            )

        # 5. Check total inventory cap
        total = self._maker.total_abs_inventory
        if total >= self._maker.max_total_inventory:
            await self._cancel_q.put(CancelAll("*"))
            log.warning(f"TOTAL INVENTORY CAP: ${total:.0f} — ALL quotes pulled")

        # 6. Check daily loss — use MTM P&L so net-long inventory doesn't false-trigger
        if self._app is not None:
            async with self._app._lock:
                markets = dict(self._app.markets)
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

    async def run(self):
        """Main loop — process fills from queue."""
        while True:
            fill = await self._fills_q.get()
            try:
                await self.handle_fill(fill)
            except Exception as exc:
                log.exception(f"InventoryManager error: {exc}")
