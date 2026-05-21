"""OrderManager actor — places/cancels limit orders via py-clob-client."""

import asyncio
import time
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
        app_state=None,
    ):
        self._maker = maker_state
        self._clob = clob
        self._paper = paper
        self._quote_intents_q = quote_intents_q
        self._cancel_q = cancel_q
        self._app = app_state

    def _question(self, token_id: str) -> str:
        if self._app is None:
            return ""
        cs = self._app.markets.get(token_id)
        return cs.question if cs else ""

    def handle_ladder_sync(self, update: LadderUpdate) -> None:
        """Diff-based ladder update: only cancel/replace levels whose price or size changed.

        When a level's bid price, ask price, bid size, or ask size is unchanged
        (within 0.5 shares and 0.0001 price), the existing order IDs are reused
        and no API calls are made for that level. This dramatically reduces
        cancel+place volume at high market counts.
        """
        existing = self._maker.live_orders.get(update.token_id, [])
        new_levels = []

        for i, intent in enumerate(update.levels):
            old = existing[i] if i < len(existing) else None

            price_unchanged = (
                old is not None
                and abs(old.get("bid_price", -1) - intent.bid_price) < 0.0001
                and abs(old.get("ask_price", -1) - intent.ask_price) < 0.0001
                and abs(old.get("bid_size", -1) - intent.bid_size) < 0.5
                and abs(old.get("ask_size", -1) - intent.ask_size) < 0.5
            )

            if price_unchanged:
                # Reuse existing order IDs — no API calls needed for this level
                new_levels.append(old)
                continue

            # Cancel the old level before placing new orders
            if old is not None:
                self._cancel_level(old, update.token_id)

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

        # Cancel any surplus old levels (ladder shrank)
        for old in existing[len(update.levels):]:
            self._cancel_level(old, update.token_id)

        self._maker.live_orders[update.token_id] = new_levels

        question = self._question(update.token_id)
        level_strs = [
            f"bid={lv['bid_price']:.3f}x{lv['bid_size']:.0f} ask={lv['ask_price']:.3f}x{lv['ask_size']:.0f}"
            for lv in new_levels
        ]
        log.info(
            f"LADDER {'[PAPER] ' if self._paper else ''}"
            f"[{update.token_id[:12]}] levels={len(new_levels)} reason={update.reason} "
            f"{' | '.join(level_strs)}"
            + (f" | q={question[:60]!r}" if question else "")
        )

    def handle_cancel_sync(self, cancel: CancelAll) -> None:
        if cancel.is_global:
            if not self._paper and self._clob:
                try:
                    self._clob.cancel_all()
                except Exception as exc:
                    body = ""
                    try:
                        if hasattr(exc, "response") and exc.response is not None:
                            body = f" | response={exc.response.text[:200]}"
                    except Exception:
                        pass
                    log.warning(f"cancel_all failed (continuing): {exc}{body}")
            n = sum(len(v) for v in self._maker.live_orders.values())
            self._maker.live_orders.clear()
            self._maker.total_cancels += n
            log.warning("CANCEL ALL — all quotes pulled")
        else:
            levels = self._maker.live_orders.pop(cancel.token_id, [])
            for level in levels:
                self._cancel_level(level, cancel.token_id)
            self._maker.total_cancels += len(levels)
            question = self._question(cancel.token_id)
            log.info(
                f"CANCEL [{cancel.token_id[:12]}] {len(levels)} levels"
                + (f" | q={question[:60]!r}" if question else "")
            )

    def _place_one(self, token_id: str, price: float, size: float, side: str) -> str:
        if size <= 0:
            return ""
        if self._paper:
            return f"paper-{token_id[:8]}-{side.lower()}-{price:.4f}"

        # SELL YES requires holding YES tokens (unavailable on a fresh account).
        # Convert to BUY NO at the complementary price — economically identical,
        # requires only USDC collateral.
        actual_token_id = token_id
        actual_side = side
        actual_price = price
        if side == "SELL" and self._app is not None:
            cs = self._app.markets.get(token_id)
            if cs and cs.no_token_id:
                actual_token_id = cs.no_token_id
                actual_side = "BUY"
                actual_price = round(1.0 - price, 4)

        from trading.clob_factory import OrderArgs, OrderType
        order_args = OrderArgs(token_id=actual_token_id, price=actual_price, size=size, side=actual_side)
        t0 = time.time()
        try:
            signed = self._clob.create_order(order_args)
            result = self._clob.post_order(signed, order_type=OrderType.GTC, post_only=True)
            order_id = result.get("orderID", "")
            status = result.get("status", "UNKNOWN")
            latency_ms = (time.time() - t0) * 1000
            question = self._question(token_id)
            log.info(
                f"ORDER OK | id={order_id} {side} {size:.0f}sh@{price:.4f} "
                f"[{token_id[:12]}] latency={latency_ms:.0f}ms status={status}"
                + (f" | q={question[:60]!r}" if question else "")
            )
            return order_id
        except Exception as exc:
            latency_ms = (time.time() - t0) * 1000
            body = ""
            try:
                if hasattr(exc, "response") and exc.response is not None:
                    body = f" | response={exc.response.text[:300]}"
            except Exception:
                pass
            question = self._question(token_id)
            log.warning(
                f"ORDER FAIL | {side} {size:.0f}sh@{price:.4f} [{token_id[:12]}] "
                f"latency={latency_ms:.0f}ms: {exc}{body}"
                + (f" | q={question[:60]!r}" if question else "")
            )
            return ""

    def _cancel_level(self, level: dict, token_id: str = "") -> None:
        ids = [
            level.get("bid_order_id", ""),
            level.get("ask_order_id", ""),
        ]
        ids = [oid for oid in ids if oid and not oid.startswith("paper-")]
        if not ids:
            return
        if self._paper:
            return
        if self._clob:
            try:
                self._clob.cancel_orders(ids)
                log.info(f"CANCEL ORDERS [{token_id[:12] or '?'}] ids={ids}")
            except Exception as exc:
                body = ""
                try:
                    if hasattr(exc, "response") and exc.response is not None:
                        body = f" | response={exc.response.text[:300]}"
                except Exception:
                    pass
                log.warning(f"CANCEL FAIL [{token_id[:12] or '?'}] ids={ids}: {exc}{body}")

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
