import json
import asyncio
from datetime import datetime, timezone
import httpx
import websockets
from config import (
    POLYMARKET_WS_URL,
    MIN_MARKET_LIQUIDITY,
    MARKET_CATEGORY_FILTER,
    MARKET_SORT_MODE,
    MAX_SUBSCRIBED_MARKETS,
    PARSEABLE_MARKET_RESERVE,
)
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


def _allowed_categories() -> set[str]:
    raw = (MARKET_CATEGORY_FILTER or "").strip()
    if not raw:
        return set()
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def _sort_key(meta: dict) -> tuple:
    volume = float(meta.get("volume") or 0.0)
    volume_24h = float(meta.get("volume_24h") or 0.0)
    liquidity = float(meta.get("liquidity") or 0.0)
    expiry = meta.get("expiry")
    expiry_ts = expiry.timestamp() if expiry else float("inf")
    now_ts = datetime.now(timezone.utc).timestamp()
    time_left = max(expiry_ts - now_ts, 0.0) if expiry else float("inf")

    if MARKET_SORT_MODE == "volume24h_desc":
        return (-volume_24h, -volume, time_left, meta["question"])
    if MARKET_SORT_MODE == "liquidity_desc":
        return (-liquidity, -volume_24h, -volume, time_left, meta["question"])
    if MARKET_SORT_MODE == "expiry_asc":
        return (time_left, -volume_24h, -volume, meta["question"])
    if MARKET_SORT_MODE == "hybrid":
        return (-volume_24h, -liquidity, time_left, -volume, meta["question"])
    return (-volume, -volume_24h, time_left, meta["question"])


def select_markets(token_map: dict) -> dict:
    allowed_categories = _allowed_categories()
    candidates = [
        (yes_id, meta)
        for yes_id, meta in token_map.items()
        if not allowed_categories or meta["category"] in allowed_categories
    ]
    candidates.sort(key=lambda item: _sort_key(item[1]))
    if MAX_SUBSCRIBED_MARKETS <= 0:
        return dict(candidates)

    parseable = [item for item in candidates if item[1].get("parseable")]
    others = [item for item in candidates if not item[1].get("parseable")]

    reserve = min(PARSEABLE_MARKET_RESERVE, MAX_SUBSCRIBED_MARKETS)
    selected = parseable[:reserve]

    remaining = MAX_SUBSCRIBED_MARKETS - len(selected)
    if remaining > 0:
        remaining_pool = others + parseable[len(selected):]
        selected.extend(remaining_pool[:remaining])
    return dict(selected)


async def fetch_active_markets(client: httpx.AsyncClient) -> dict:
    """
    Fetch active order-book markets from Gamma API.
    Returns: {yes_token_id: market metadata for subscription + trading}
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

            volume = float(m.get("volumeClob") or m.get("volume") or 0)
            volume_24h = float(m.get("volume24hrClob") or m.get("volume24hr") or 0)
            liquidity = float(m.get("liquidityClob") or m.get("liquidity") or 0)

            # outcomePrices[0] is the YES probability from the last trade/AMM price
            # Use it to synthesize a tight spread when the CLOB order book is empty
            outcome_prices_raw = m.get("outcomePrices") or []
            if isinstance(outcome_prices_raw, str):
                try:
                    outcome_prices_raw = json.loads(outcome_prices_raw)
                except Exception:
                    outcome_prices_raw = []
            yes_price = float(outcome_prices_raw[0]) if outcome_prices_raw else None

            raw_bid = float(m.get("bestBid") or 0)
            raw_ask = float(m.get("bestAsk") or 1)

            if (raw_ask - raw_bid) > 0.5 and yes_price is not None and 0.03 < yes_price < 0.97:
                # Empty order book — synthesize ±2c spread from outcome price
                raw_bid = round(yes_price - 0.02, 4)
                raw_ask = round(yes_price + 0.02, 4)

            token_map[yes_id] = {
                "question":    question,
                "category":    parsed.category if parsed.parseable else "unknown",
                "expiry":      parsed.expiry or _parse_datetime(m.get("endDateIso") or m.get("endDate")),
                "parseable":   parsed.parseable,
                "no_token_id": no_id,
                "volume":      volume,
                "volume_24h":  volume_24h,
                "liquidity":   liquidity,
                "best_bid":    raw_bid,
                "best_ask":    raw_ask,
            }

        offset += _PAGE_LIMIT

    parseable_count = sum(1 for meta in token_map.values() if meta["parseable"])
    log.info(
        f"found {len(token_map)} active order-book markets from Gamma API "
        f"({parseable_count} parseable by strategy)"
    )
    return token_map


def _parse_datetime(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


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
        token_map = select_markets(token_map)

        if not token_map:
            log.warning("no active order-book markets selected; sleeping 5 min")
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

        parseable_seeded = sum(
            1
            for yes_id in self._state.markets
            if token_map.get(yes_id, {}).get("parseable")
        )
        log.info(
            f"seeded {len(self._state.markets)} liquid markets into state "
            f"({parseable_seeded} parseable by strategy)"
        )

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
        new_bid = float(bids[0]["price"]) if bids else None
        new_ask = float(asks[0]["price"]) if asks else None
        # Only update if the book update gives a meaningful spread (< 50%)
        # Ignore near-empty quotes like bid=0.001 ask=0.999
        if new_bid is not None and new_ask is not None and (new_ask - new_bid) < 0.5:
            cs.best_bid = new_bid
            cs.best_ask = new_ask
        elif new_bid is not None and new_ask is None and new_bid > 0.01:
            cs.best_bid = new_bid
        elif new_ask is not None and new_bid is None and new_ask < 0.99:
            cs.best_ask = new_ask
        # Compute book depth (sum of best 5 levels each side)
        if bids:
            cs.bid_depth = sum(float(b.get("size", 0)) for b in bids[:5])
        if asks:
            cs.ask_depth = sum(float(a.get("size", 0)) for a in asks[:5])
        if new_bid is not None or new_ask is not None:
            self._state.stamp_feed("clob")

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
                self._state.stamp_feed("clob")
