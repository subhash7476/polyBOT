import json
import asyncio
import websockets
from config import POLYMARKET_WS_URL, MIN_MARKET_LIQUIDITY
from feeds.base import BaseFeed
from market.state import AppState, ContractState
from market.contract_filter import is_crypto_finance_market, meets_liquidity_threshold
from utils.logger import get_logger

log = get_logger(__name__)

_SUBSCRIBE_MSG = {"type": "subscribe", "channel": "market"}


class CLOBMonitor(BaseFeed):
    """Maintains live ContractState for all crypto/finance Polymarket markets."""

    def __init__(self, state: AppState):
        super().__init__("clob_monitor")
        self._state = state

    async def _run(self):
        async with websockets.connect(POLYMARKET_WS_URL, ping_interval=20) as ws:
            await ws.send(json.dumps(_SUBSCRIBE_MSG))
            self.log.info("subscribed to Polymarket CLOB WebSocket")
            async for raw in ws:
                msg = json.loads(raw)
                await self._handle(msg)

    async def _handle(self, msg: dict):
        event_type = msg.get("event_type", "")
        if event_type == "book":
            await self._handle_book(msg)
        elif event_type == "price_change":
            await self._handle_price(msg)

    async def _handle_book(self, msg: dict):
        market = msg.get("market", {})
        if not is_crypto_finance_market(market):
            return
        yes_token_id = msg.get("asset_id", "")
        no_token_id  = msg.get("no_asset_id", "")  # Polymarket provides both
        if not yes_token_id:
            return
        bids = msg.get("bids", [])
        asks = msg.get("asks", [])
        cs = ContractState(
            yes_token_id=yes_token_id,
            no_token_id=no_token_id,
            question=market.get("question", ""),
            category=market.get("category", "").lower(),
            best_bid=float(bids[0]["price"]) if bids else 0.0,
            best_ask=float(asks[0]["price"]) if asks else 1.0,
            volume_usd=float(market.get("volume", 0)),
        )
        if meets_liquidity_threshold(cs, MIN_MARKET_LIQUIDITY):
            await self._state.upsert_market(cs)

    async def _handle_price(self, msg: dict):
        yes_token_id = msg.get("asset_id", "")
        if not yes_token_id:
            return
        async with self._state._lock:
            cs = self._state.markets.get(yes_token_id)
            if cs:
                side  = msg.get("side", "")
                price = float(msg.get("price", 0))
                if side == "BUY":
                    cs.best_bid = price
                elif side == "SELL":
                    cs.best_ask = price
