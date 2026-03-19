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
        Compute next-meeting cut probability from FRED + NY Fed free data.
        Sources (no API key required):
          - FRED CSV: current target rate lower/upper bounds
          - NY Fed SOFR: overnight rate (proxy for money market expectations)
          - FRED CSV: CPI and unemployment for Taylor-rule λ estimate
        """
        try:
            # Fetch Fed funds target bounds from FRED CSV (no API key needed)
            r_lower = await client.get(
                "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFEDTARL", timeout=10.0
            )
            r_upper = await client.get(
                "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFEDTARU", timeout=10.0
            )
            lower = float(r_lower.text.strip().split("\n")[-1].split(",")[1])
            upper = float(r_upper.text.strip().split("\n")[-1].split(",")[1])
            target_mid = (lower + upper) / 2.0

            # SOFR from NY Fed
            r_sofr = await client.get(
                "https://markets.newyorkfed.org/read?productCode=50&eventCodes=520"
                "&limit=5&startPosition=0&sort=postDt:-1&format=json",
                timeout=10.0,
            )
            sofr = float(r_sofr.json()["refRates"][0]["percentRate"])
            await self._state.update_feeds(sofr=sofr)

            # CPI and unemployment from FRED CSV (no key needed)
            # CPI YoY: fetch 13 months of CPIAUCSL index to compute 12-month change
            r_cpi = await client.get(
                "https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCSL", timeout=10.0
            )
            r_unemp = await client.get(
                "https://fred.stlouisfed.org/graph/fredgraph.csv?id=UNRATE", timeout=10.0
            )
            cpi_lines = [l for l in r_cpi.text.strip().split("\n") if not l.startswith("DATE") and "." in l]
            unemp_lines = [l for l in r_unemp.text.strip().split("\n") if not l.startswith("DATE") and "." in l]
            # Compute YoY CPI from index (last value vs 12 months ago)
            if len(cpi_lines) >= 13:
                idx_now = float(cpi_lines[-1].split(",")[1])
                idx_yago = float(cpi_lines[-13].split(",")[1])
                cpi = round((idx_now / idx_yago - 1.0) * 100, 2) if idx_yago else 3.2
            elif cpi_lines:
                cpi = float(cpi_lines[-1].split(",")[1])  # fallback: raw index
            else:
                cpi = 3.2
            unrate = float(unemp_lines[-1].split(",")[1]) if unemp_lines else 4.0

            # Expected cuts remaining in 2026 (Poisson λ)
            # Taylor-inspired: high inflation → fewer cuts; high unemployment → more cuts
            cpi_factor = max(0.0, 1.0 - (cpi - 2.0) / 4.0)      # 1.0 at 2%, 0 at 6%
            unemp_factor = max(0.0, (unrate - 3.5) / 2.0)         # 0 at 3.5%, 1.0 at 5.5%
            lambda_cuts = max(0.1, 2.0 * cpi_factor + unemp_factor)

            await self._state.update_feeds(fed_expected_cuts=lambda_cuts)

            # Implied single-meeting cut probability ≈ 1 - P(hold at next meeting)
            # SOFR below target lower bound → market pricing in cuts soon
            sofr_spread = sofr - lower   # negative = SOFR below lower bound → bullish for cuts
            cut_prob = max(0.05, min(0.95, 0.5 - sofr_spread * 2.0))

            log.info(
                f"fed model: target={lower:.2f}-{upper:.2f} SOFR={sofr:.2f} "
                f"CPI={cpi:.1f} UNRATE={unrate:.1f} λ={lambda_cuts:.2f} "
                f"cut_prob={cut_prob:.2f}"
            )
            return cut_prob

        except Exception as exc:
            log.warning(f"fed model fetch failed: {exc}")
            return 0.5
