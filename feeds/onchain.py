import asyncio
import math
import httpx
from typing import Optional
from config import ONCHAIN_POLL_INTERVAL, GLASSNODE_API_KEY
from market.state import AppState
from utils.logger import get_logger

log = get_logger(__name__)

# --- Glassnode (paid — kept for backward compat, skipped if no key) ---
_GLASSNODE_BASE = "https://api.glassnode.com"
_RANGES = {
    "btc_exchange_netflow":    (-15_000, 15_000),
    "btc_sopr":                (0.90, 1.10),
    "btc_nupl":                (-0.5, 0.75),
    "btc_whale_ratio":         (0.0, 0.5),
    "stablecoin_supply_ratio": (0.0, 0.15),
}
_ENDPOINTS = {
    "btc_exchange_netflow":    "/v1/metrics/transactions/transfers_volume_exchanges_net",
    "btc_sopr":                "/v1/metrics/indicators/sopr",
    "btc_nupl":                "/v1/metrics/indicators/unrealized_profit_loss",
    "btc_whale_ratio":         "/v1/metrics/transactions/transfers_volume_miners_to_exchanges",
    "stablecoin_supply_ratio": "/v1/metrics/stablecoins/stablecoin_supply_ratio",
}
_INVERT = {"btc_exchange_netflow"}

# --- Free data sources ---
DEFI_LLAMA_STABLES = "https://stablecoins.llama.fi/stablecoinchains"
BLOCKCHAIN_COM_BASE = "https://api.blockchain.info"


def normalise(value: float, lo: float, hi: float, invert: bool = False) -> float:
    raw = (value - lo) / (hi - lo) if (hi - lo) else 0.5
    raw = max(0.0, min(1.0, raw))
    return round(1.0 - raw if invert else raw, 4)


def parse_timeseries(data: list) -> Optional[float]:
    if not data:
        return None
    return data[-1].get("v")


def _compute_stablecoin_signal(current: float, prev: float) -> float:
    """
    30-day stablecoin supply change as normalised signal [-1, 1].
    Rising supply = new money entering = bullish.
    """
    if prev == 0:
        return 0.0
    pct_change = (current - prev) / prev  # e.g. 0.11 = +11%
    return float(max(-1.0, min(1.0, math.tanh(pct_change * 10))))


def _compute_hashrate_signal(current: float, month_ago: float) -> float:
    """
    30-day hash rate trend as normalised signal [-1, 1].
    Rising hash rate = miner confidence = bullish.
    """
    if month_ago == 0:
        return 0.0
    pct_change = (current - month_ago) / month_ago
    return float(max(-1.0, min(1.0, math.tanh(pct_change * 10))))


class OnChainFeed:
    def __init__(self, state: AppState):
        self._state = state
        self._prev_stablecoin_supply: Optional[float] = None

    async def start(self):
        log.info("starting")
        async with httpx.AsyncClient(timeout=15.0) as client:
            # Double-fetch stablecoin supply on startup so the diff signal
            # is available immediately (first sets _prev, second computes it)
            try:
                await self._fetch_stablecoin_supply(client)
                await asyncio.sleep(2)
                await self._fetch_stablecoin_supply(client)
                await self._fetch_btc_hash_rate(client)
                if GLASSNODE_API_KEY:
                    await self._fetch_glassnode(client)
            except Exception as exc:
                log.warning(f"on-chain startup fetch error: {exc}")

            while True:
                try:
                    await self._fetch_free_sources(client)
                    if GLASSNODE_API_KEY:
                        await self._fetch_glassnode(client)
                except Exception as exc:
                    log.warning(f"on-chain fetch error: {exc}")
                await asyncio.sleep(ONCHAIN_POLL_INTERVAL)

    async def _fetch_free_sources(self, client: httpx.AsyncClient):
        """DeFiLlama stablecoin supply + Blockchain.com hash rate — no API key needed."""
        await self._fetch_stablecoin_supply(client)
        await self._fetch_btc_hash_rate(client)

    async def _fetch_stablecoin_supply(self, client: httpx.AsyncClient):
        """
        DeFiLlama: total stablecoin supply on Ethereum + Tron.
        Rising supply = new money entering = bullish signal.
        """
        try:
            r = await client.get(DEFI_LLAMA_STABLES, timeout=15.0)
            r.raise_for_status()
            data = r.json()
            # Sum total circulating across all chains
            total = 0.0
            for chain in data:
                total += float(chain.get("totalCirculatingUSD", {}).get("peggedUSD", 0) or 0)
            if total == 0:
                return
            if self._prev_stablecoin_supply is not None:
                signal = _compute_stablecoin_signal(total, self._prev_stablecoin_supply)
                await self._state.update_feeds(stablecoin_supply_change=signal)
                log.debug(f"stablecoin supply=${total/1e9:.1f}B signal={signal:.3f}")
            self._prev_stablecoin_supply = total
        except Exception as e:
            log.warning(f"DeFiLlama stablecoin fetch failed: {e}")

    async def _fetch_btc_hash_rate(self, client: httpx.AsyncClient):
        """
        Blockchain.com: 30-day hash rate trend.
        Rising hash rate = miner confidence = bullish.
        """
        try:
            r = await client.get(
                f"{BLOCKCHAIN_COM_BASE}/charts/hash-rate",
                params={"timespan": "30days", "format": "json"},
                timeout=15.0,
            )
            r.raise_for_status()
            values = r.json().get("values", [])
            if len(values) >= 2:
                current = float(values[-1]["y"])
                month_ago = float(values[0]["y"])
                signal = _compute_hashrate_signal(current, month_ago)
                await self._state.update_feeds(btc_hashrate_trend=signal)
                log.debug(f"hashrate signal={signal:.3f}")
        except Exception as e:
            log.warning(f"Blockchain.com hashrate fetch failed: {e}")

    async def _fetch_glassnode(self, client: httpx.AsyncClient):
        updates = {}
        for key, path in _ENDPOINTS.items():
            try:
                resp = await client.get(
                    f"{_GLASSNODE_BASE}{path}",
                    params={"a": "BTC", "i": "24h", "api_key": GLASSNODE_API_KEY},
                )
                resp.raise_for_status()
                raw = parse_timeseries(resp.json())
                if raw is not None:
                    lo, hi = _RANGES[key]
                    updates[key] = normalise(raw, lo, hi, invert=(key in _INVERT))
                    log.debug(f"{key}={updates[key]:.3f} (raw={raw:.4f})")
            except Exception as exc:
                log.warning(f"glassnode {key}: {exc}")
        if updates:
            await self._state.update_feeds(**updates)
