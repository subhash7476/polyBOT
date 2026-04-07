"""MarkoutTracker — measures post-fill price movement for adverse-selection detection.

For each fill, schedules price checks at T+5s, T+30s, T+60s against the mid
at fill time. Results are written to fills_markout.jsonl and aggregated into
rolling stats accessible via .stats for the dashboard.

Markout sign convention (same scale for BUY and SELL):
    markout = sign * (mid_later - mid_at_fill)
    where sign = +1 for BUY, -1 for SELL

Positive markout → price moved our way (spread capture / luck).
Negative markout → adverse selection (taker knew something).
"""

import asyncio
import heapq
import json
import time
from collections import deque
from dataclasses import dataclass

from market.state import AppState
from maker.types import Fill
from utils.logger import get_logger

log = get_logger(__name__)

MARKOUT_INTERVALS = (5, 30, 60)  # seconds post-fill
_ROLLING_WINDOW = 500            # fills kept in rolling stats per interval


@dataclass
class _PendingCheck:
    check_at: float   # unix timestamp when this check is due
    interval: int     # 5, 30, or 60
    token_id: str
    side: str
    mid_at_fill: float
    fill_price: float
    filled_at: float
    size: float
    order_id: str

    def __lt__(self, other: "_PendingCheck") -> bool:
        return self.check_at < other.check_at


class MarkoutTracker:
    """Async actor that samples market mid at T+5/30/60 after each fill."""

    def __init__(
        self,
        app_state: AppState,
        markout_q: asyncio.Queue,
        log_path: str = "fills_markout.jsonl",
    ):
        self._app = app_state
        self._markout_q = markout_q
        self._log_path = log_path
        self._heap: list[_PendingCheck] = []  # min-heap ordered by check_at

        # Rolling deques of markout values per interval
        self._markouts: dict[int, deque] = {
            i: deque(maxlen=_ROLLING_WINDOW) for i in MARKOUT_INTERVALS
        }
        self._n_fills = 0

    # ------------------------------------------------------------------
    # Public stats (read by dashboard_loop)
    # ------------------------------------------------------------------

    @property
    def stats(self) -> dict:
        """Rolling adverse-selection stats for the dashboard."""
        result: dict = {"markout_fills": self._n_fills}
        for interval in MARKOUT_INTERVALS:
            data = list(self._markouts[interval])
            n = len(data)
            if n:
                avg = sum(data) / n
                adverse = sum(1 for x in data if x < 0) / n
            else:
                avg = 0.0
                adverse = 0.0
            result[f"avg_markout_{interval}s"] = round(avg, 5)
            result[f"adverse_rate_{interval}s"] = round(adverse, 3)
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sign(side: str) -> float:
        return 1.0 if side == "BUY" else -1.0

    def _schedule(self, fill: Fill) -> None:
        for interval in MARKOUT_INTERVALS:
            check = _PendingCheck(
                check_at=fill.filled_at + interval,
                interval=interval,
                token_id=fill.token_id,
                side=fill.side,
                mid_at_fill=fill.mid_at_fill,
                fill_price=fill.price,
                filled_at=fill.filled_at,
                size=fill.size,
                order_id=fill.order_id,
            )
            heapq.heappush(self._heap, check)

    async def _evaluate(self, check: _PendingCheck) -> None:
        async with self._app._lock:
            cs = self._app.markets.get(check.token_id)
            mid_now = cs.mid if cs else None

        if mid_now is None:
            return  # market gone — skip silently

        markout = self._sign(check.side) * (mid_now - check.mid_at_fill)
        self._markouts[check.interval].append(markout)

        record = {
            "order_id": check.order_id,
            "token_id": check.token_id[:16],
            "side": check.side,
            "fill_price": check.fill_price,
            "size": check.size,
            "mid_at_fill": check.mid_at_fill,
            "mid_now": mid_now,
            "interval_s": check.interval,
            "markout": round(markout, 6),
            "filled_at": check.filled_at,
            "checked_at": time.time(),
        }
        try:
            with open(self._log_path, "a") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError as exc:
            log.warning(f"MarkoutTracker: could not write log: {exc}")

        log.info(
            f"MARKOUT T+{check.interval}s [{check.token_id[:8]}] "
            f"{check.side} markout={markout:+.4f} "
            f"mid {check.mid_at_fill:.4f}→{mid_now:.4f}"
        )

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        while True:
            # Process all due checks
            now = time.time()
            while self._heap and self._heap[0].check_at <= now:
                check = heapq.heappop(self._heap)
                await self._evaluate(check)

            # Sleep until the next due check or 1s, whichever comes first,
            # but wake immediately if a new fill arrives.
            sleep_for = 1.0
            if self._heap:
                sleep_for = min(sleep_for, max(0.01, self._heap[0].check_at - time.time()))

            try:
                fill = await asyncio.wait_for(self._markout_q.get(), timeout=sleep_for)
                self._schedule(fill)
                self._n_fills += 1
            except asyncio.TimeoutError:
                pass
