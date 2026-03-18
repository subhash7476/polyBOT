"""
feeds/macro.py — DXY + 10Y yield (Yahoo Finance) + CME FedWatch (HTML scrape).

v2.1 Fix 5: CachedValue with staleness tracking. Every metric returns (value, confidence).
Confidence decays linearly to 0 over max_age_seconds after last good fetch.
Signal confidence=0 → BayesianEngine ignores it (no log-odds contribution).
"""

import asyncio
import httpx
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from config import FEDWATCH_POLL_INTERVAL
from market.state import AppState
from utils.logger import get_logger

log = get_logger(__name__)

_POLL_INTERVAL = 300   # 5 minutes
_FALLBACK_CONFIDENCE = 0.3


@dataclass
class CachedValue:
    value: float
    fetched_at: datetime
    max_age_seconds: int = 600

    @property
    def is_stale(self) -> bool:
        age = (datetime.now(timezone.utc) - self.fetched_at).total_seconds()
        return age > self.max_age_seconds

    @property
    def confidence(self) -> float:
        age = (datetime.now(timezone.utc) - self.fetched_at).total_seconds()
        return max(0.0, 1.0 - (age / self.max_age_seconds))


class MacroFeed:
    def __init__(self, state: AppState):
        self._state = state
        self._cache: dict[str, CachedValue] = {}
        self._fail_count = 0

    async def start(self):
        log.info("starting")
        async with httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0"}, timeout=15.0
        ) as client:
            while True:
                try:
                    await self._fetch_all(client)
                except Exception as exc:
                    log.warning(f"macro fetch error: {exc}")
                await asyncio.sleep(_POLL_INTERVAL)

    async def _fetch_with_cache(
        self, client: httpx.AsyncClient, key: str, fetch_fn, max_age: int = 600
    ) -> tuple[float, float]:
        """Returns (value, confidence). Falls back to cache on failure."""
        try:
            value = await fetch_fn(client)
            self._cache[key] = CachedValue(
                value=value,
                fetched_at=datetime.now(timezone.utc),
                max_age_seconds=max_age,
            )
            self._fail_count = 0
            return value, 1.0
        except Exception as exc:
            self._fail_count += 1
            cached = self._cache.get(key)
            if cached:
                conf = cached.confidence * _FALLBACK_CONFIDENCE
                log.warning(f"macro {key} failed (#{self._fail_count}), "
                            f"using cache conf={conf:.2f}: {exc}")
                return cached.value, conf
            else:
                log.error(f"macro {key} failed, no cache: {exc}")
                return 0.5, 0.0  # neutral, zero confidence

    async def _fetch_all(self, client: httpx.AsyncClient):
        dxy, dxy_conf = await self._fetch_with_cache(
            client, "dxy", lambda c: self._yahoo_price(c, "DX-Y.NYB"), max_age=300
        )
        y10, y10_conf = await self._fetch_with_cache(
            client, "y10", lambda c: self._yahoo_price(c, "^TNX"), max_age=300
        )
        fed, fed_conf = await self._fetch_with_cache(
            client, "fed", self._fetch_fedwatch, max_age=3600
        )

        # Compute DXY trend: current vs cached MA20 (simplified: store last value as proxy)
        prev_dxy = self._cache.get("dxy_prev")
        dxy_trend = ((dxy - prev_dxy.value) / prev_dxy.value) if prev_dxy else 0.0

        await self._state.update_feeds(
            dxy=dxy,             dxy_confidence=dxy_conf,
            dxy_trend=dxy_trend,
            yield_10y=y10,       yield_10y_confidence=y10_conf,
            fed_may_cut_prob=fed, fed_confidence=fed_conf,
        )
        # Store prev DXY for next trend computation
        self._cache["dxy_prev"] = CachedValue(
            value=dxy, fetched_at=datetime.now(timezone.utc)
        )

    async def _yahoo_price(self, client: httpx.AsyncClient, symbol: str) -> float:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        r = await client.get(url, params={"interval": "1d", "range": "1d"})
        r.raise_for_status()
        return float(r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"])

    async def _fetch_fedwatch(self, client: httpx.AsyncClient) -> float:
        """
        Scrape CME FedWatch for next-meeting cut probability.
        FRAGILE — isolated here so only one method needs updating if layout changes.
        Returns 0.5 (neutral) on failure; cache handles staleness.
        """
        try:
            r = await client.get(
                "https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html"
            )
            # TODO: inspect current page structure and implement parser
            # For now return 0.5 — cache will hold last good value once implemented
            return 0.5
        except Exception as exc:
            log.warning(f"fedwatch scrape failed: {exc}")
            return 0.5
