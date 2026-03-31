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
    FAST_RESOLVE_PRIORITY,
    MODEL_CATEGORY_MIN_SLOTS,
)
from feeds.base import BaseFeed
from market.state import AppState, ContractState
from market.contract_filter import meets_liquidity_threshold
from engine.contract_parser import parse_contract, _parse_expiry as _explicit_expiry
from engine.arb_scanner import ThresholdMarket
from engine.flatline import record_price as record_flatline_price
from engine.orderbook_imbalance import record_obi_reading
from engine.volume_divergence import record_volume
from utils.logger import get_logger

log = get_logger(__name__)

_GAMMA_URL = "https://gamma-api.polymarket.com/markets"
_GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
_PAGE_LIMIT = 100
_MAX_PAGES = 30  # scan up to 3,000 markets


def _is_weather_market(meta: dict) -> bool:
    q = (meta.get("question") or "").lower()
    return "highest temperature" in q or "lowest temperature" in q or meta.get("category") == "weather"


def _is_ultra_short_crypto(meta: dict) -> bool:
    q = (meta.get("question") or "").lower()
    return any(kw in q for kw in ["next 5 minutes", "next 15 minutes", "go up in", "go down in"])


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

    # Boost priority for markets expiring within 72h — flatline needs pre-resolution data.
    # Markets in [0, 24h) get a higher bonus (+3) than those in [24h, 72h) (+2).
    hours_left = time_left / 3600
    if 0 < hours_left < 24:
        short_dated_bonus = -3  # negative so lower sort value = higher priority
    elif 24 <= hours_left < 72:
        short_dated_bonus = -2
    else:
        short_dated_bonus = 0

    # Additional boost for fast-resolving categories
    if FAST_RESOLVE_PRIORITY:
        if _is_weather_market(meta):
            short_dated_bonus -= 1
        if _is_ultra_short_crypto(meta):
            short_dated_bonus = min(short_dated_bonus, -3)  # treat as same priority as <24h markets

    if MARKET_SORT_MODE == "volume24h_desc":
        return (short_dated_bonus, -volume_24h, -volume, time_left, meta["question"])
    if MARKET_SORT_MODE == "liquidity_desc":
        return (short_dated_bonus, -liquidity, -volume_24h, -volume, time_left, meta["question"])
    if MARKET_SORT_MODE == "expiry_asc":
        return (time_left, short_dated_bonus, -volume_24h, -volume, meta["question"])
    if MARKET_SORT_MODE == "hybrid":
        return (short_dated_bonus, -volume_24h, -liquidity, time_left, -volume, meta["question"])
    return (short_dated_bonus, -volume, -volume_24h, time_left, meta["question"])


_MODEL_CATEGORIES = frozenset({"crypto", "weather", "rates", "macro"})


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

    # Guarantee minimum slots for categories with feed-based models (crypto, weather,
    # rates, macro). Without this, high-volume election/event markets crowd them out
    # entirely, leaving the bot with 0 tradeable signals.
    model_parseable = [item for item in parseable if item[1]["category"] in _MODEL_CATEGORIES]
    other_parseable = [item for item in parseable if item[1]["category"] not in _MODEL_CATEGORIES]

    n_model = min(MODEL_CATEGORY_MIN_SLOTS, len(model_parseable), reserve)
    model_selected = model_parseable[:n_model]

    remaining_reserve = reserve - len(model_selected)
    other_selected = other_parseable[:remaining_reserve]

    selected = model_selected + other_selected

    # Fill any remaining WS slots with non-parseable markets
    remaining = MAX_SUBSCRIBED_MARKETS - len(selected)
    if remaining > 0:
        selected_ids = {yes_id for yes_id, _ in selected}
        overflow = [item for item in parseable if item[0] not in selected_ids]
        remaining_pool = others + overflow
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
                "condition_id": m.get("conditionId", "") or "",
                "volume":      volume,
                "volume_24h":  volume_24h,
                "liquidity":   liquidity,
                "best_bid":    raw_bid,
                "best_ask":    raw_ask,
            }

        offset += _PAGE_LIMIT

    # Supplement with daily temperature bucket markets from the weather events endpoint.
    # These are negRisk markets that don't appear in the standard /markets pagination
    # (they sit beyond offset 13,000+ and would require 130+ pages to reach).
    weather_markets = await _fetch_weather_event_markets(client)
    new_weather = {k: v for k, v in weather_markets.items() if k not in token_map}
    token_map.update(new_weather)

    # Supplement with crypto Up/Down and ATH markets from the crypto-prices events endpoint.
    # These are also buried deep in pagination; use start_date_min to fetch only current markets.
    crypto_event_markets = await _fetch_crypto_event_markets(client)
    new_crypto = {k: v for k, v in crypto_event_markets.items() if k not in token_map}
    token_map.update(new_crypto)

    parseable_count = sum(1 for meta in token_map.values() if meta["parseable"])
    log.info(
        f"found {len(token_map)} active order-book markets from Gamma API "
        f"({parseable_count} parseable by strategy, {len(new_weather)} weather + "
        f"{len(new_crypto)} crypto event markets added)"
    )
    return token_map


async def _fetch_weather_event_markets(client: httpx.AsyncClient) -> dict:
    """
    Fetch daily temperature bucket markets from the weather events endpoint.
    These are negRisk markets grouped by city+date event; each event has 11 buckets.
    The standard /markets pagination does not surface these within the first 13k results.
    """
    token_map = {}
    try:
        resp = await client.get(_GAMMA_EVENTS_URL, params={
            "tag_slug": "weather", "active": "true", "closed": "false", "limit": 100,
        }, timeout=20.0)
        resp.raise_for_status()
        events = resp.json()
        if isinstance(events, dict):
            events = events.get("data", [])
    except Exception as exc:
        log.warning(f"weather events fetch error: {exc}")
        return token_map

    for event in events:
        if "Highest temperature" not in event.get("title", ""):
            continue
        for m in event.get("markets", []):
            if not m.get("acceptingOrders") or not m.get("enableOrderBook"):
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

            outcome_prices_raw = m.get("outcomePrices") or []
            if isinstance(outcome_prices_raw, str):
                try:
                    outcome_prices_raw = json.loads(outcome_prices_raw)
                except Exception:
                    outcome_prices_raw = []
            yes_price = float(outcome_prices_raw[0]) if outcome_prices_raw else None

            # negRisk markets: bestBid/bestAsk from the AMM are reliable; CLOB book is near-empty
            raw_bid = float(m.get("bestBid") or 0)
            raw_ask = float(m.get("bestAsk") or 1)
            if (raw_ask - raw_bid) > 0.5 and yes_price is not None and 0.03 < yes_price < 0.97:
                raw_bid = round(yes_price - 0.02, 4)
                raw_ask = round(yes_price + 0.02, 4)

            token_map[yes_id] = {
                "question":    question,
                "category":    "weather",  # forced — all events here are temp markets
                "expiry":      parsed.expiry or _parse_datetime(m.get("endDateIso") or m.get("endDate")),
                "parseable":   parsed.parseable,
                "no_token_id": no_id,
                "condition_id": m.get("conditionId", "") or "",
                "volume":      float(m.get("volumeClob") or m.get("volume") or 0),
                "volume_24h":  float(m.get("volume24hrClob") or m.get("volume24hr") or 0),
                "liquidity":   float(m.get("liquidityClob") or m.get("liquidity") or 0),
                "best_bid":    raw_bid,
                "best_ask":    raw_ask,
                "neg_risk":    True,
                "fees_enabled": False,  # weather markets have no taker fee
            }

    log.debug(f"weather events: found {len(token_map)} temperature bucket markets")
    return token_map


async def _fetch_crypto_event_markets(client: httpx.AsyncClient) -> dict:
    """
    Fetch crypto Up/Down and ATH markets from the crypto-prices events endpoint.

    The Gamma events API stores these at deep pagination offsets (offset 200+) when
    sorted by ID ascending. Using start_date_min=yesterday directly returns only
    current and near-future markets without paging through ~200 stale December 2025
    events. API expiry (endDateIso) is used as ground truth — bypasses the question
    parser's year-inference for same-day short-window markets.
    """
    from datetime import timedelta
    token_map = {}
    now = datetime.now(timezone.utc)
    # Fetch markets that started within the last 2 days — catches overnight markets
    # and any pre-created upcoming ones.
    start_min = (now - timedelta(days=2)).strftime("%Y-%m-%d")

    try:
        resp = await client.get(_GAMMA_EVENTS_URL, params={
            "tag_slug": "crypto-prices",
            "active": "true",
            "closed": "false",
            "limit": 200,
            "start_date_min": start_min,
        }, timeout=20.0)
        resp.raise_for_status()
        events = resp.json()
        if isinstance(events, dict):
            events = events.get("data", [])
    except Exception as exc:
        log.warning(f"crypto events fetch error: {exc}")
        return token_map

    skipped_expired = 0
    for event in events:
        for m in event.get("markets", []):
            if not m.get("acceptingOrders") or not m.get("enableOrderBook"):
                continue
            token_ids_raw = m.get("clobTokenIds", "[]")
            try:
                token_ids = json.loads(token_ids_raw) if isinstance(token_ids_raw, str) else token_ids_raw
            except Exception:
                continue
            if len(token_ids) < 2:
                continue

            # API endDateIso is authoritative — avoids question-parser year mis-inference
            api_expiry = _parse_datetime(m.get("endDateIso") or m.get("endDate"))
            if api_expiry is not None:
                exp = api_expiry if api_expiry.tzinfo else api_expiry.replace(tzinfo=timezone.utc)
                if exp < now:
                    skipped_expired += 1
                    continue

            yes_id, no_id = token_ids[0], token_ids[1]
            question = m.get("question", "")
            parsed = parse_contract(yes_id, question)
            if not parsed.parseable or parsed.category != "crypto":
                continue

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
                raw_bid = round(yes_price - 0.02, 4)
                raw_ask = round(yes_price + 0.02, 4)

            token_map[yes_id] = {
                "question":      question,
                "category":      "crypto",
                "strategy_type": parsed.strategy_type,
                "expiry":        api_expiry or parsed.expiry,
                "parseable":     True,
                "no_token_id":   no_id,
                "condition_id":  m.get("conditionId", "") or "",
                "volume":        float(m.get("volumeClob") or m.get("volume") or 0),
                "volume_24h":    float(m.get("volume24hrClob") or m.get("volume24hr") or 0),
                "liquidity":     float(m.get("liquidityClob") or m.get("liquidity") or 0),
                "best_bid":      raw_bid,
                "best_ask":      raw_ask,
            }

    log.debug(
        f"crypto events: found {len(token_map)} price markets ({skipped_expired} expired skipped)"
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
        # Skip contracts with no explicit date — default EOM expiry produces false arb groupings
        # (e.g. "before GTA VI" grouped with "by March 31" contracts)
        if not _explicit_expiry(cs.question):
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

    _REDISCOVERY_INTERVAL = 15 * 60  # seconds between market re-discovery runs

    async def _run(self):
        # Outer loop: re-discover markets every 15 minutes, then re-subscribe.
        while True:
            async with httpx.AsyncClient() as client:
                token_map = await fetch_active_markets(client)
            token_map = select_markets(token_map)

            if not token_map:
                log.warning("no active order-book markets selected; sleeping 5 min")
                await asyncio.sleep(300)
                continue

            # Seed state from Gamma snapshot — skip markets already in state to
            # preserve live price data accumulated since bot started.
            async with self._state._lock:
                existing_ids = set(self._state.markets.keys())

            new_count = 0
            for yes_id, meta in token_map.items():
                if yes_id in existing_ids:
                    continue  # preserve existing ContractState (live WS prices)
                cs = ContractState(
                    yes_token_id=yes_id,
                    no_token_id=meta["no_token_id"],
                    question=meta["question"],
                    category=meta["category"],
                    best_bid=meta["best_bid"],
                    best_ask=meta["best_ask"],
                    volume_usd=meta["volume"],
                    condition_id=meta.get("condition_id", ""),
                    neg_risk=meta.get("neg_risk", False),
                    fees_enabled=meta.get("fees_enabled", True),
                )
                if meets_liquidity_threshold(cs, MIN_MARKET_LIQUIDITY):
                    await self._state.upsert_market(cs)
                    new_count += 1

            parseable_seeded = sum(
                1
                for yes_id in self._state.markets
                if token_map.get(yes_id, {}).get("parseable")
            )
            log.info(
                f"seeded {new_count} new liquid markets into state "
                f"(total {len(self._state.markets)}, {parseable_seeded} parseable by strategy)"
            )

            token_ids = list(token_map.keys())
            subscribe_msg = {
                "assets_ids": token_ids,
                "type": "Market",
                "id": "1",
            }

            # Inner loop: handle WS messages until 15-minute re-discovery window elapses
            # or a connection error forces a reconnect (which stays in the inner loop).
            discovery_deadline = asyncio.get_event_loop().time() + self._REDISCOVERY_INTERVAL
            while asyncio.get_event_loop().time() < discovery_deadline:
                try:
                    time_remaining = discovery_deadline - asyncio.get_event_loop().time()
                    async with websockets.connect(POLYMARKET_WS_URL, ping_interval=20) as ws:
                        await ws.send(json.dumps(subscribe_msg))
                        self.log.info(
                            f"subscribed to {len(token_ids)} markets on Polymarket CLOB WebSocket "
                            f"(re-discovery in {time_remaining/60:.0f}m)"
                        )
                        async for raw in ws:
                            payload = json.loads(raw)
                            events = payload if isinstance(payload, list) else [payload]
                            for msg in events:
                                await self._handle(msg)
                            # Check if re-discovery window has elapsed
                            if asyncio.get_event_loop().time() >= discovery_deadline:
                                break
                except Exception as exc:
                    self.log.warning(f"WS error: {exc} — reconnecting in 10s")
                    # HEARTBEAT SAFETY: if disconnect > 60s, consider cancelling open orders.
                    # Currently handled by reconnect loop + paper mode position TTL.
                    # TODO (Phase 4D): on live mode, call executor.cancel_all_open_orders()
                    # if time since last stamp_feed("clob") > 60 seconds.
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
        # Record OBI and volume for all subscribed markets (feeds flatline + signals)
        record_obi_reading(yes_token_id, cs)
        record_volume(yes_token_id, cs)

    async def _handle_price(self, msg: dict):
        yes_token_id = msg.get("asset_id", "")
        if not yes_token_id:
            return
        new_mid = None
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
                new_mid = cs.mid
        # Record price for all subscribed markets so flatline has history before trading
        if new_mid is not None:
            record_flatline_price(yes_token_id, new_mid)
