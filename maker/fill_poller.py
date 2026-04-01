"""FillPoller actor — detects order fills at 500ms cadence."""

import asyncio
import time
from maker.state import MakerState
from maker.types import Fill
from market.state import AppState, ContractState
from utils.logger import get_logger

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
    ) -> list[Fill]:
        """Check if any live paper quotes have been crossed by the market."""
        fills = []
        now = time.time()

        for token_id, order_info in list(maker_state.live_orders.items()):
            cs = markets.get(token_id)
            if not cs:
                continue

            bid_price = order_info.get("bid_price", 0.0)
            ask_price = order_info.get("ask_price", 1.0)
            bid_size = order_info.get("bid_size", 0.0)
            ask_size = order_info.get("ask_size", 0.0)

            # Someone's ask <= our bid → our bid fills (we buy)
            if cs.best_ask <= bid_price and bid_price > 0:
                fills.append(Fill(
                    token_id=token_id,
                    side="BUY",
                    price=bid_price,
                    size=bid_size,
                    order_id=order_info.get("bid_order_id", ""),
                    filled_at=now,
                ))

            # Someone's bid >= our ask → our ask fills (we sell)
            if cs.best_bid >= ask_price and ask_price < 1.0:
                fills.append(Fill(
                    token_id=token_id,
                    side="SELL",
                    price=ask_price,
                    size=ask_size,
                    order_id=order_info.get("ask_order_id", ""),
                    filled_at=now,
                ))

        return fills

    async def _run_paper(self):
        while True:
            async with self._app._lock:
                markets = dict(self._app.markets)

            fills = self.check_paper_fills(self._maker, markets)
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
