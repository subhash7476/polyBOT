"""FillPoller actor — detects order fills at 500ms cadence."""

import asyncio
import math
import random
import time
from maker.state import MakerState
from maker.types import Fill
from market.state import AppState, ContractState
from utils.logger import get_logger

# Assumed competition on each side (other makers at same level).
# Higher = fewer fills per unit of volume.
_COMPETITION_FACTOR = 10.0

log = get_logger(__name__)


class FillPoller:
    """Polls for fills — live via get_orders(), paper via book crossing."""

    POLL_INTERVAL = 0.5  # 500ms

    def __init__(
        self,
        app_state: AppState,
        maker_state: MakerState,
        fills_q: asyncio.Queue,
        clob=None,
        paper: bool = True,
    ):
        self._app = app_state
        self._maker = maker_state
        self._fills_q = fills_q
        self._clob = clob
        self._paper = paper
        self._order_states: dict[str, str] = {}

    @staticmethod
    def check_paper_fills(
        maker_state: MakerState,
        markets: dict[str, ContractState],
        poll_interval: float = 0.5,
    ) -> list[Fill]:
        """Simulate fills for paper quotes using a volume-based Poisson model.

        As a maker we post INSIDE the market spread (bid < market_ask, ask > market_bid).
        A taker preferring our price fills us; we model that as a Poisson process:
            rate = market_volume_usd_per_day / (86400 * quote_size * COMPETITION_FACTOR)
        Only the competitive side of each quote is eligible:
            bid eligible: our bid > market best_bid  (we're the best bid)
            ask eligible: our ask < market best_ask  (we're the best ask)
        """
        fills = []
        now = time.time()

        for token_id, levels in list(maker_state.live_orders.items()):
            cs = markets.get(token_id)
            if not cs or cs.volume_usd <= 0:
                continue

            for level in levels:
                bid_price = level.get("bid_price", 0.0)
                ask_price = level.get("ask_price", 1.0)
                bid_size = level.get("bid_size", 0.0)
                ask_size = level.get("ask_size", 0.0)
                bid_order_id = level.get("bid_order_id", "")
                ask_order_id = level.get("ask_order_id", "")

                # Poisson fill probability for this poll window
                size_ref = max(bid_size, ask_size, 1.0)
                rate_per_sec = cs.volume_usd / (86400.0 * size_ref * _COMPETITION_FACTOR)
                p_fill = 1.0 - math.exp(-rate_per_sec * poll_interval)

                mid = cs.mid

                # Bid fills when our bid is at or better than the market's best bid.
                # >= (not >) so that joining the queue (posting exactly at best_bid)
                # also generates paper fills — this is valid for book-relative quotes
                # that compress to the market spread on tight markets.
                if (
                    bid_order_id
                    and bid_size > 0
                    and bid_price >= cs.best_bid
                    and bid_price > 0
                    and random.random() < p_fill
                ):
                    fills.append(Fill(
                        token_id=token_id,
                        side="BUY",
                        price=bid_price,
                        size=bid_size,
                        order_id=bid_order_id,
                        filled_at=now,
                        mid_at_fill=mid,
                    ))

                # Ask fills when our ask is at or better than the market's best ask.
                # <= (not <) to match the bid-side change above.
                if (
                    ask_order_id
                    and ask_size > 0
                    and ask_price <= cs.best_ask
                    and ask_price < 1.0
                    and random.random() < p_fill
                ):
                    fills.append(Fill(
                        token_id=token_id,
                        side="SELL",
                        price=ask_price,
                        size=ask_size,
                        order_id=ask_order_id,
                        filled_at=now,
                        mid_at_fill=mid,
                    ))

        return fills

    @staticmethod
    def consume_paper_fills(maker_state: MakerState, fills: list[Fill]) -> None:
        """Remove filled paper order sides so one synthetic order cannot fill twice."""
        for fill in fills:
            levels = maker_state.live_orders.get(fill.token_id, [])
            updated_levels = []
            for level in levels:
                if fill.side == "BUY" and level.get("bid_order_id", "") == fill.order_id:
                    level = dict(level)
                    level["bid_order_id"] = ""
                    level["bid_size"] = 0.0
                elif fill.side == "SELL" and level.get("ask_order_id", "") == fill.order_id:
                    level = dict(level)
                    level["ask_order_id"] = ""
                    level["ask_size"] = 0.0

                has_bid = bool(level.get("bid_order_id", "")) and level.get("bid_size", 0.0) > 0
                has_ask = bool(level.get("ask_order_id", "")) and level.get("ask_size", 0.0) > 0
                if has_bid or has_ask:
                    updated_levels.append(level)

            if updated_levels:
                maker_state.live_orders[fill.token_id] = updated_levels
            else:
                maker_state.live_orders.pop(fill.token_id, None)

    async def _run_paper(self):
        while True:
            async with self._app._lock:
                markets = dict(self._app.markets)

            fills = self.check_paper_fills(self._maker, markets, poll_interval=self.POLL_INTERVAL)
            self.consume_paper_fills(self._maker, fills)
            for fill in fills:
                log.info(
                    f"PAPER FILL: {fill.side} {fill.size:.2f} @ {fill.price:.3f} "
                    f"[{fill.token_id[:8]}]"
                )
                await self._fills_q.put(fill)

            await asyncio.sleep(self.POLL_INTERVAL)

    async def _run_live(self):
        from trading.clob_factory import OpenOrderParams

        # Seed known order IDs as OPEN so fills on first poll after restart are detected
        for levels in self._maker.live_orders.values():
            for level in levels:
                for key in ("bid_order_id", "ask_order_id"):
                    oid = level.get(key, "")
                    if oid and not oid.startswith("paper-"):
                        self._order_states[oid] = "OPEN"

        while True:
            try:
                orders = self._clob.get_open_orders(OpenOrderParams())
                for order in orders:
                    oid = order.get("orderID", "")
                    status = order.get("status", "")
                    prev = self._order_states.get(oid)

                    if prev in ("OPEN", "live") and status in ("MATCHED", "FILLED"):
                        asset_id = order.get("asset_id", "")
                        async with self._app._lock:
                            cs = self._app.markets.get(asset_id)
                            mid = cs.mid if cs else 0.0
                        fill = Fill(
                            token_id=asset_id,
                            side=order.get("side", "BUY"),
                            price=float(order.get("price", 0)),
                            size=float(order.get("size_matched", order.get("original_size", 0))),
                            order_id=oid,
                            filled_at=time.time(),
                            mid_at_fill=mid,
                        )
                        log.info(
                            f"FILL: {fill.side} {fill.size:.2f} @ {fill.price:.3f} "
                            f"[{fill.token_id[:8]}]"
                        )
                        await self._fills_q.put(fill)

                    self._order_states[oid] = status

            except Exception as exc:
                log.warning(f"FillPoller error: {exc}")

            await asyncio.sleep(self.POLL_INTERVAL)

    async def run(self):
        if self._paper:
            await self._run_paper()
        else:
            await self._run_live()
