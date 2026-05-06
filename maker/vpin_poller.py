"""maker/vpin_poller.py — Polls CLOB /trades per market, computes size-weighted VPIN."""

import asyncio
import time
from collections import deque

import httpx

from config import POLYMARKET_CLOB_URL
from market.state import AppState
from utils.logger import get_logger

log = get_logger(__name__)

VPIN_POLL_INTERVAL = 30.0    # seconds between polls per market
VPIN_STALE_SECONDS = 180.0   # reset to neutral if no update within this window
MIN_VPIN_TRADES = 10         # minimum trades in buffer before computing VPIN
VPIN_EMA_ALPHA = 0.3         # weight on new observation; 0.7 on previous smoothed value
_BUFFER_SIZE = 100           # rolling buffer depth per token
_LOG_HEARTBEAT_CYCLES = 10   # log liveness every N poll cycles (~5 min)


def _ema(prev: float, raw: float, alpha: float = VPIN_EMA_ALPHA) -> float:
    """Exponential moving average: (1-alpha)*prev + alpha*raw."""
    return (1.0 - alpha) * prev + alpha * raw


def _compute_raw_vpin(trades: list[dict]) -> float:
    """Compute size-weighted VPIN from a list of classified trade dicts.

    Each dict must have 'size' (str or float) and either:
      - 'side': 'BUY' or 'SELL' (explicit), or
      - 'price' only (tick-rule classification applied).

    Returns 0.5 (neutral) if fewer than MIN_VPIN_TRADES or zero volume.
    """
    if len(trades) < MIN_VPIN_TRADES:
        return 0.5

    classified: list[tuple[str, float]] = []
    prev_price: float | None = None
    last_side = "BUY"

    for t in trades:
        size = float(t.get("size", 0) or 0)
        if "side" in t:
            side = t["side"]
        else:
            price = float(t.get("price", 0) or 0)
            if prev_price is None or price == prev_price:
                side = last_side
            elif price > prev_price:
                side = "BUY"
            else:
                side = "SELL"
            prev_price = price
        last_side = side
        classified.append((side, size))

    buy_vol  = sum(sz for side, sz in classified if side == "BUY")
    sell_vol = sum(sz for side, sz in classified if side == "SELL")
    total_vol = buy_vol + sell_vol

    if total_vol == 0.0:
        return 0.5

    return abs(buy_vol - sell_vol) / total_vol


class VPINPoller:
    """Polls CLOB /trades endpoint per active market and writes smoothed VPIN
    into AppState.markets[token_id].vpin under the existing market lock.

    Runs as an independent asyncio task. Per-token failures are caught and
    isolated — a bad token does not affect other markets or the main loop.
    """

    def __init__(self, app_state: AppState):
        self._app = app_state
        self._buffers: dict[str, deque] = {}
        self._last_trade_id: dict[str, str] = {}
        self._vpin_cache: dict[str, tuple[float, float]] = {}  # token -> (vpin, updated_at)
        self._cycle = 0

    def _filter_new_trades(self, token_id: str, trades: list[dict]) -> list[dict]:
        """Return only trades newer than last_trade_id for this token."""
        last_id = self._last_trade_id.get(token_id)
        if last_id is None:
            return trades
        new = []
        for t in trades:
            if t.get("id") == last_id:
                break
            new.append(t)
        return new

    def get_vpin(self, token_id: str) -> float:
        """Return cached VPIN, resetting to 0.5 if stale."""
        entry = self._vpin_cache.get(token_id)
        if entry is None:
            return 0.5
        vpin, updated_at = entry
        if time.time() - updated_at > VPIN_STALE_SECONDS:
            return 0.5
        return vpin

    async def _poll_token(self, client: httpx.AsyncClient, token_id: str) -> None:
        """Fetch new trades for one token and update VPIN in AppState."""
        try:
            resp = await client.get(
                f"{POLYMARKET_CLOB_URL}/trades",
                params={"token_id": token_id, "limit": 100},
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
            trades_raw: list[dict] = data if isinstance(data, list) else data.get("data", [])
        except Exception as exc:
            log.debug(f"vpin_poller: fetch failed [{token_id[:8]}]: {exc}")
            return

        new_trades = self._filter_new_trades(token_id, trades_raw)
        if new_trades:
            self._last_trade_id[token_id] = new_trades[0].get("id", "")

        buf = self._buffers.setdefault(token_id, deque(maxlen=_BUFFER_SIZE))
        buf.extend(reversed(new_trades))  # API returns newest-first; store oldest-first for tick rule

        # Only refresh the cache timestamp when new trades arrived. On quiet markets
        # the timestamp ages past VPIN_STALE_SECONDS and get_vpin() returns 0.5
        # (neutral) — preventing stale non-neutral VPIN from persisting indefinitely.
        if not new_trades:
            return

        raw_vpin = _compute_raw_vpin(list(buf))
        prev_vpin = self._vpin_cache.get(token_id, (0.5, 0.0))[0]
        smoothed = _ema(prev=prev_vpin, raw=raw_vpin)

        now = time.time()
        self._vpin_cache[token_id] = (smoothed, now)

        async with self._app._lock:
            cs = self._app.markets.get(token_id)
            if cs is not None:
                cs.vpin = smoothed
                cs.vpin_updated_at = now

    async def run(self) -> None:
        """Main loop — poll all active markets every VPIN_POLL_INTERVAL seconds."""
        log.info(f"VPINPoller started (poll_interval={VPIN_POLL_INTERVAL}s, stale={VPIN_STALE_SECONDS}s)")
        async with httpx.AsyncClient() as client:
            while True:
                try:
                    async with self._app._lock:
                        token_ids = list(self._app.markets.keys())

                    for token_id in token_ids:
                        try:
                            await self._poll_token(client, token_id)
                        except Exception as exc:
                            log.debug(f"vpin_poller: token error [{token_id[:8]}]: {exc}")

                    self._cycle += 1
                    if self._cycle % _LOG_HEARTBEAT_CYCLES == 0:
                        log.info(
                            f"vpin_poller: heartbeat cycle={self._cycle} "
                            f"tracking {len(token_ids)} tokens"
                        )

                except Exception as exc:
                    log.exception(f"vpin_poller: top-level error (will retry): {exc}")

                await asyncio.sleep(VPIN_POLL_INTERVAL)
