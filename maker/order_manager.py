"""OrderManager actor — places/cancels limit orders via py-clob-client."""

import asyncio
from maker.state import MakerState
from maker.types import LadderUpdate, CancelAll
from utils.logger import get_logger

log = get_logger(__name__)


class OrderManager:
    """Translates LadderUpdates into CLOB API calls."""

    def __init__(
        self,
        maker_state: MakerState,
        clob=None,
        paper: bool = True,
        quote_intents_q: asyncio.Queue | None = None,
        cancel_q: asyncio.Queue | None = None,
    ):
        self._maker = maker_state
        self._clob = clob
        self._paper = paper
        self._quote_intents_q = quote_intents_q
        self._cancel_q = cancel_q

    def handle_ladder_sync(self, update: LadderUpdate) -> None:
        """Cancel all existing levels for this market, then place the new ladder."""
        existing_levels = self._maker.live_orders.get(update.token_id, [])
        for level in existing_levels:
            self._cancel_level(level)

        new_levels = []
        for intent in update.levels:
            bid_oid = self._place_one(intent.token_id, intent.bid_price, intent.bid_size, "BUY")
            ask_oid = self._place_one(intent.token_id, intent.ask_price, intent.ask_size, "SELL")
            new_levels.append({
                "bid_order_id": bid_oid,
                "ask_order_id": ask_oid,
                "bid_price": intent.bid_price,
                "ask_price": intent.ask_price,
                "bid_size": intent.bid_size,
                "ask_size": intent.ask_size,
            })

        self._maker.live_orders[update.token_id] = new_levels
        log.info(
            f"LADDER {'[PAPER] ' if self._paper else ''}"
            f"[{update.token_id[:8]}] levels={len(new_levels)} "
            f"reason={update.reason}"
        )

    def handle_cancel_sync(self, cancel: CancelAll) -> None:
        if cancel.is_global:
            if not self._paper and self._clob:
                self._clob.cancel_all()
            n = sum(len(v) for v in self._maker.live_orders.values())
            self._maker.live_orders.clear()
            self._maker.total_cancels += n
            log.warning("CANCEL ALL — all quotes pulled")
        else:
            levels = self._maker.live_orders.pop(cancel.token_id, [])
            for level in levels:
                self._cancel_level(level)
            self._maker.total_cancels += len(levels)
            log.info(f"CANCEL [{cancel.token_id[:8]}] — {len(levels)} levels")

    def _place_one(self, token_id: str, price: float, size: float, side: str) -> str:
        if size <= 0:
            return ""
        if self._paper:
            return f"paper-{token_id[:8]}-{side.lower()}-{price:.4f}"

        from trading.clob_factory import OrderArgs, OrderType
        order_args = OrderArgs(token_id=token_id, price=price, size=size, side=side)
        try:
            signed = self._clob.create_order(order_args)
            result = self._clob.post_order(signed, orderType=OrderType.GTC, post_only=True)
            return result.get("orderID", "")
        except Exception as exc:
            log.warning(f"place_order failed ({side} {size:.4f}@{price:.4f} [{token_id[:8]}]): {exc}")
            return ""

    def _cancel_level(self, level: dict) -> None:
        if self._paper:
            return
        ids = [
            level.get("bid_order_id", ""),
            level.get("ask_order_id", ""),
        ]
        ids = [oid for oid in ids if oid and not oid.startswith("paper-")]
        if ids and self._clob:
            self._clob.cancel_orders(ids)

    async def run(self):
        """Main async loop — process ladder updates and cancels."""
        while True:
            # Process cancels with priority
            while self._cancel_q and not self._cancel_q.empty():
                try:
                    cancel = self._cancel_q.get_nowait()
                    self.handle_cancel_sync(cancel)
                except asyncio.QueueEmpty:
                    break

            # Process one ladder update
            if self._quote_intents_q:
                try:
                    update = await asyncio.wait_for(
                        self._quote_intents_q.get(), timeout=0.1
                    )
                    self.handle_ladder_sync(update)
                except asyncio.TimeoutError:
                    pass
                except Exception as exc:
                    log.error(f"handle_ladder_sync error: {exc}", exc_info=True)
            else:
                await asyncio.sleep(0.1)
