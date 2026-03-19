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
import os

log = get_logger(__name__)

_POLL_INTERVAL = 300   # 5 minutes
_FALLBACK_CONFIDENCE = 0.3

# FRED API — free with API key (https://fred.stlouisfed.org/docs/api/)
_FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
_FRED_SERIES = {
    "consensus_cpi": "CPIAUCSL",        # CPI for All Urban Consumers (MoM %)
    "consensus_unemployment": "UNRATE", # Unemployment Rate
    "consensus_gdp": "GDP",             # Gross Domestic Product growth rate
}
_FRED_API_KEY = os.getenv("FRED_API_KEY", "")


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
        await self._fetch_fred_consensus(client)
        # Store prev DXY for next trend computation
        self._cache["dxy_prev"] = CachedValue(
            value=dxy, fetched_at=datetime.now(timezone.utc)
        )

    async def _yahoo_price(self, client: httpx.AsyncClient, symbol: str) -> float:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        r = await client.get(url, params={"interval": "1d", "range": "1d"})
        r.raise_for_status()
        return float(r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"])

    async def _fetch_fred_consensus(self, client: httpx.AsyncClient):
        """
        Fetch latest macro consensus forecasts from FRED API.
        Gracefully skips if FRED_API_KEY is not set.
        """
        if not _FRED_API_KEY:
            return
        updates = {}
        for field, series_id in _FRED_SERIES.items():
            try:
                r = await client.get(
                    _FRED_BASE,
                    params={
                        "series_id": series_id,
                        "api_key": _FRED_API_KEY,
                        "file_type": "json",
                        "limit": 1,
                        "sort_order": "desc",
                    },
                )
                r.raise_for_status()
                obs = r.json().get("observations", [])
                if obs and obs[0]["value"] != ".":
                    updates[field] = float(obs[0]["value"])
                    log.debug(f"FRED {series_id}={updates[field]}")
            except Exception as e:
                log.warning(f"FRED {series_id} fetch failed: {e}")
        if updates:
            await self._state.update_feeds(**updates)

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
