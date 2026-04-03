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

                # Poisson fill probability for this poll window
                size_ref = max(bid_size, ask_size, 1.0)
                rate_per_sec = cs.volume_usd / (86400.0 * size_ref * _COMPETITION_FACTOR)
                p_fill = 1.0 - math.exp(-rate_per_sec * poll_interval)

                # Bid fills when our bid is better than (above) the market's best bid
                if bid_price > cs.best_bid and bid_price > 0 and random.random() < p_fill:
                    fills.append(Fill(
                        token_id=token_id,
                        side="BUY",
                        price=bid_price,
                        size=bid_size,
                        order_id=level.get("bid_order_id", ""),
                        filled_at=now,
                    ))

                # Ask fills when our ask is better than (below) the market's best ask
                if ask_price < cs.best_ask and ask_price < 1.0 and random.random() < p_fill:
                    fills.append(Fill(
                        token_id=token_id,
                        side="SELL",
                        price=ask_price,
                        size=ask_size,
                        order_id=level.get("ask_order_id", ""),
                        filled_at=now,
                    ))

        return fills

    async def _run_paper(self):
        while True:
            async with self._app._lock:
                markets = dict(self._app.markets)

            fills = self.check_paper_fills(self._maker, markets, poll_interval=self.POLL_INTERVAL)
            for fill in fills:
                log.info(
                    f"PAPER FILL: {fill.side} {fill.size:.2f} @ {fill.price:.3f} "
                    f"[{fill.token_id[:8]}]"
                )
                await self._fills_q.put(fill)

            await asyncio.sleep(self.POLL_INTERVAL)

    async def _run_live(self):
        from py_clob_client.clob_types import OpenOrderParams

        while True:
            try:
                orders = self._clob.get_orders(OpenOrderParams())
                for order in orders:
                    oid = order.get("orderID", "")
                    status = order.get("status", "")
                    prev = self._order_states.get(oid)

                    if prev in ("OPEN", "live") and status in ("MATCHED", "FILLED"):
                        fill = Fill(
                            token_id=order.get("asset_id", ""),
                            side=order.get("side", "BUY"),
                            price=float(order.get("price", 0)),
                            size=float(order.get("size_matched", order.get("original_size", 0))),
                            order_id=oid,
                            filled_at=time.time(),
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
