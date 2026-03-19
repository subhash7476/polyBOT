import json
import asyncio
import httpx
import websockets
from config import POLYMARKET_WS_URL, MIN_MARKET_LIQUIDITY
from feeds.base import BaseFeed
from market.state import AppState, ContractState
from market.contract_filter import meets_liquidity_threshold
from engine.contract_parser import parse_contract
from engine.arb_scanner import ThresholdMarket
from utils.logger import get_logger

log = get_logger(__name__)

_GAMMA_URL = "https://gamma-api.polymarket.com/markets"
_PAGE_LIMIT = 100
_MAX_PAGES = 30  # scan up to 3,000 markets


async def fetch_active_markets(client: httpx.AsyncClient) -> dict:
    """
    Fetch active order-book markets from Gamma API.
    Returns: {yes_token_id: {question, category, no_token_id, volume, best_bid, best_ask}}
    """
    token_map = {}
    offset = 0

    for page in range(_MAX_PAGES):
        try:
            resp = await client.get(_GAMMA_URL, params={
                "active": "true", "closed": "false",
                "enableOrderBook": "true",
                "limit": _PAGE_LIMIT, "offset": offset,
            }, timeout=20.0)
            resp.raise_for_status()
            markets = resp.json()
            if not isinstance(markets, list):
                markets = markets.get("data", [])
        except Exception as exc:
            log.warning(f"Gamma fetch error (page {page+1}): {exc}")
            break

        if not markets:
            break

        for m in markets:
            if not m.get("acceptingOrders"):
                continue
            token_ids_raw = m.get("clobTokenIds", "[]")
            try:
                token_ids = json.loads(token_ids_raw) if isinstance(token_ids_raw, str) else token_ids_raw
            except Exception:
                continue
            if len(token_ids) < 2:
                continue

            yes_id, no_id = token_ids[0], token_ids[1]
            question = m.get("question", "")
            parsed = parse_contract(yes_id, question)
            if not parsed.parseable:
                continue

            volume = float(m.get("volumeClob") or m.get("volume") or 0)
            token_map[yes_id] = {
                "question":    question,
                "category":    parsed.category,
                "no_token_id": no_id,
                "volume":      volume,
                "best_bid":    float(m.get("bestBid") or 0),
                "best_ask":    float(m.get("bestAsk") or 1),
            }

        offset += _PAGE_LIMIT

    log.info(f"found {len(token_map)} parseable crypto/price markets from Gamma API")
    return token_map


def build_threshold_markets(markets: dict) -> list:
    """Convert active ContractState entries into ThresholdMarket objects for arb scanning."""
    result = []
    for yes_id, cs in markets.items():
        parsed = parse_contract(yes_id, cs.question)
        if not parsed.parseable or not parsed.target_price or not parsed.expiry:
            continue
        if parsed.category != "crypto":
            continue
        expiry_key = parsed.expiry.strftime("%b%Y").lower()  # e.g. "mar2026"
        result.append(ThresholdMarket(
            token_id=yes_id,
            asset=parsed.asset,
            target=parsed.target_price,
            direction=parsed.direction,
            expiry_key=expiry_key,
            yes_price=cs.mid,
            no_token_id=cs.no_token_id,
        ))
    return result


class CLOBMonitor(BaseFeed):
    """Maintains live ContractState for all crypto/finance Polymarket markets."""

    def __init__(self, state: AppState):
        super().__init__("clob_monitor")
        self._state = state

    async def _run(self):
        # Fetch markets once — persists across WebSocket reconnects
        async with httpx.AsyncClient() as client:
            token_map = await fetch_active_markets(client)

        if not token_map:
            log.warning("no parseable crypto price markets found — sleeping 5 min")
            await asyncio.sleep(300)
            return

        # Seed state from Gamma snapshot so trading loop has data immediately
        for yes_id, meta in token_map.items():
            cs = ContractState(
                yes_token_id=yes_id,
                no_token_id=meta["no_token_id"],
                question=meta["question"],
                category=meta["category"],
                best_bid=meta["best_bid"],
                best_ask=meta["best_ask"],
                volume_usd=meta["volume"],
            )
            if meets_liquidity_threshold(cs, MIN_MARKET_LIQUIDITY):
                await self._state.upsert_market(cs)

        log.info(f"seeded {len(self._state.markets)} liquid markets into state")

        token_ids = list(token_map.keys())
        subscribe_msg = {
            "assets_ids": token_ids,
            "type": "Market",
            "id": "1",
        }

        # Reconnect loop — token_map and state seed are NOT repeated on reconnect
        while True:
            try:
                async with websockets.connect(POLYMARKET_WS_URL, ping_interval=20) as ws:
                    await ws.send(json.dumps(subscribe_msg))
                    self.log.info(f"subscribed to {len(token_ids)} markets on Polymarket CLOB WebSocket")
                    async for raw in ws:
                        payload = json.loads(raw)
                        events = payload if isinstance(payload, list) else [payload]
                        for msg in events:
                            await self._handle(msg)
            except Exception as exc:
                self.log.warning(f"WS error: {exc} — reconnecting in 10s")
                await asyncio.sleep(10)

    async def _handle(self, msg: dict):
        event_type = msg.get("event_type", "")
        if event_type == "book":
            await self._handle_book(msg)
        elif event_type == "price_change":
            await self._handle_price(msg)

    async def _handle_book(self, msg: dict):
        yes_token_id = msg.get("asset_id", "")
        async with self._state._lock:
            cs = self._state.markets.get(yes_token_id)
            if not cs:
                return
        bids = msg.get("bids", [])
        asks = msg.get("asks", [])
        if bids:
            cs.best_bid = float(bids[0]["price"])
        if asks:
            cs.best_ask = float(asks[0]["price"])

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
