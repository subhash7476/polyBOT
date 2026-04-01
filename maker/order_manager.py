"""OrderManager actor — places/cancels limit orders via py-clob-client."""

import asyncio
from maker.state import MakerState
from maker.types import QuoteIntent, CancelAll
from utils.logger import get_logger

log = get_logger(__name__)


class OrderManager:
    """Translates QuoteIntents into CLOB API calls."""

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

    def handle_quote_intent_sync(self, intent: QuoteIntent) -> None:
        """Place or replace quotes for a market."""
        existing = self._maker.live_orders.get(intent.token_id)

        if existing:
            self._cancel_pair(existing)

        bid_oid = self._place_one(intent.token_id, intent.bid_price, intent.bid_size, "BUY")
        ask_oid = self._place_one(intent.token_id, intent.ask_price, intent.ask_size, "SELL")

        self._maker.live_orders[intent.token_id] = {
            "bid_order_id": bid_oid,
            "ask_order_id": ask_oid,
            "bid_price": intent.bid_price,
            "ask_price": intent.ask_price,
            "bid_size": intent.bid_size,
            "ask_size": intent.ask_size,
        }

        log.info(
            f"QUOTE {'[PAPER] ' if self._paper else ''}"
            f"[{intent.token_id[:8]}] bid={intent.bid_price:.3f} "
            f"ask={intent.ask_price:.3f} spread={intent.spread:.3f} "
            f"reason={intent.reason}"
        )

    def handle_cancel_sync(self, cancel: CancelAll) -> None:
        if cancel.is_global:
            if not self._paper and self._clob:
                self._clob.cancel_all()
            self._maker.live_orders.clear()
            log.warning("CANCEL ALL — all quotes pulled")
        else:
            existing = self._maker.live_orders.pop(cancel.token_id, None)
            if existing and not self._paper and self._clob:
                self._cancel_pair(existing)
            log.info(f"CANCEL [{cancel.token_id[:8]}]")

    def _place_one(self, token_id: str, price: float, size: float, side: str) -> str:
        if self._paper:
            return f"paper-{token_id[:8]}-{side.lower()}"

        from py_clob_client.clob_types import OrderArgs, OrderType

        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=side,
        )
        signed = self._clob.create_order(order_args)
        result = self._clob.post_order(signed, orderType=OrderType.GTC, post_only=True)
        return result.get("orderID", "")

    def _cancel_pair(self, order_info: dict) -> None:
        if self._paper:
            return
        ids = [
            order_info.get("bid_order_id", ""),
            order_info.get("ask_order_id", ""),
        ]
        ids = [oid for oid in ids if oid and not oid.startswith("paper-")]
        if ids:
            self._clob.cancel_orders(ids)

    async def run(self):
        """Main async loop — process intents and cancels."""
        while True:
            # Process cancels with priority
            while self._cancel_q and not self._cancel_q.empty():
                try:
                    cancel = self._cancel_q.get_nowait()
                    self.handle_cancel_sync(cancel)
                except asyncio.QueueEmpty:
                    break

            # Process one quote intent
            if self._quote_intents_q:
                try:
                    intent = await asyncio.wait_for(
                        self._quote_intents_q.get(), timeout=0.1
                    )
                    self.handle_quote_intent_sync(intent)
                except asyncio.TimeoutError:
                    pass
            else:
                await asyncio.sleep(0.1)
