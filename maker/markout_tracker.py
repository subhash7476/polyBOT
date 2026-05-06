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
from maker.state import MakerState
from maker.types import Fill
from utils.logger import get_logger

log = get_logger(__name__)

MARKOUT_INTERVALS = (5, 30, 60)  # seconds post-fill
_ROLLING_WINDOW = 500            # fills kept in rolling stats per interval

MIN_MARKET_FILLS = 20     # min per-market fills to trust per-market avg
MIN_CATEGORY_FILLS = 100  # min fills to trust per-category avg

# Live-mode adverse-selection kill switch.
# After this many fills have rolled through the 30s deque, if the rolling mean
# is worse than the threshold, the tracker raises MakerKillSwitchError, which
# propagates through asyncio.gather() and triggers the clean shutdown path in
# runner.run_maker() (checkpoint saved, fill ledger closed).
# Disabled in paper/shadow modes where markouts are synthetic.
KILL_SWITCH_MIN_FILLS = 50
KILL_SWITCH_MAX_AVG_MARKOUT_30S = -0.002   # -20 bps


class MakerKillSwitchError(RuntimeError):
    """Raised when live-mode markouts breach the adverse-selection threshold."""


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
        maker_state: MakerState,
        markout_q: asyncio.Queue,
        log_path: str = "fills_markout.jsonl",
        kill_switch_enabled: bool = False,
    ):
        self._app = app_state
        self._maker = maker_state
        self._markout_q = markout_q
        self._log_path = log_path
        self._heap: list[_PendingCheck] = []  # min-heap ordered by check_at
        self._kill_switch_enabled = kill_switch_enabled

        # Rolling deques of markout values per interval: token_id -> {interval: deque}
        self._market_markouts: dict[str, dict[int, deque]] = {}
        self._markouts: dict[int, deque] = {
            i: deque(maxlen=_ROLLING_WINDOW) for i in MARKOUT_INTERVALS
        }
        self._n_fills = 0
        # Per-category rolling markouts: category -> {interval: deque}
        self.by_category: dict[str, dict[int, deque]] = {}

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

    def get_markout_30s(self, token_id: str, category: str) -> float:
        """Rolling avg markout at T+30s with three-tier fallback.

        Returns per-market value if >= MIN_MARKET_FILLS fills exist,
        else per-category value if >= MIN_CATEGORY_FILLS fills exist,
        else 0.0 (neutral — no data yet).
        """
        per_mkt = self._market_markouts.get(token_id, {}).get(30)
        if per_mkt is not None and len(per_mkt) >= MIN_MARKET_FILLS:
            return sum(per_mkt) / len(per_mkt)
        per_cat = self.by_category.get(category, {}).get(30)
        if per_cat is not None and len(per_cat) >= MIN_CATEGORY_FILLS:
            return sum(per_cat) / len(per_cat)
        return 0.0

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
            category = cs.category if cs else ""

        if mid_now is None:
            return  # market gone — skip silently

        markout = self._sign(check.side) * (mid_now - check.mid_at_fill)
        self._markouts[check.interval].append(markout)

        # Update per-market rolling markouts for Phase 5 Aggressive Exit
        m_stats = self._market_markouts.setdefault(check.token_id, {
            i: deque(maxlen=20) for i in MARKOUT_INTERVALS
        })
        m_stats[check.interval].append(markout)
        
        # Calculate and export avg for this token/interval to MakerState
        async with self._maker._lock:
            token_avgs = self._maker.rolling_markouts.setdefault(check.token_id, {})
            d = m_stats[check.interval]
            token_avgs[check.interval] = sum(d) / len(d)

        # Update per-category rolling markouts
        if category:
            if category not in self.by_category:
                self.by_category[category] = {i: deque(maxlen=_ROLLING_WINDOW) for i in MARKOUT_INTERVALS}
            self.by_category[category][check.interval].append(markout)

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

        if (
            self._kill_switch_enabled
            and check.interval == 30
            and len(self._markouts[30]) >= KILL_SWITCH_MIN_FILLS
        ):
            data = self._markouts[30]
            avg30 = sum(data) / len(data)
            if avg30 < KILL_SWITCH_MAX_AVG_MARKOUT_30S:
                log.error(
                    f"KILL SWITCH: avg_markout_30s={avg30:+.5f} over "
                    f"{len(data)} fills < {KILL_SWITCH_MAX_AVG_MARKOUT_30S:+.5f} — "
                    f"shutting down maker bot to stop adverse-selection bleed"
                )
                raise MakerKillSwitchError(
                    f"avg_markout_30s={avg30:+.5f} < "
                    f"{KILL_SWITCH_MAX_AVG_MARKOUT_30S:+.5f} after "
                    f"{len(data)} fills"
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
