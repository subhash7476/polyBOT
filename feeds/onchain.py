import asyncio
import httpx
from typing import Optional
from config import ONCHAIN_POLL_INTERVAL, GLASSNODE_API_KEY
from market.state import AppState
from utils.logger import get_logger

log = get_logger(__name__)

_BASE = "https://api.glassnode.com"

# Historical ranges for min-max normalisation (update periodically)
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

# For netflow: outflow is bullish → high outflow = invert after normalising
_INVERT = {"btc_exchange_netflow"}


def normalise(value: float, lo: float, hi: float, invert: bool = False) -> float:
    """Map value to [0,1]. If invert=True, flip so high=bullish."""
    raw = (value - lo) / (hi - lo) if (hi - lo) else 0.5
    raw = max(0.0, min(1.0, raw))
    return round(1.0 - raw if invert else raw, 4)


def parse_timeseries(data: list) -> Optional[float]:
    if not data:
        return None
    return data[-1].get("v")


class OnChainFeed:
    def __init__(self, state: AppState):
        self._state = state

    async def start(self):
        log.info("starting")
        async with httpx.AsyncClient(timeout=15.0) as client:
            while True:
                if not GLASSNODE_API_KEY:
                    log.warning("GLASSNODE_API_KEY not set — on-chain feed inactive")
                    await asyncio.sleep(3600)
                    continue
                try:
                    await self._fetch_all(client)
                except Exception as exc:
                    log.warning(f"on-chain fetch error: {exc}")
                await asyncio.sleep(ONCHAIN_POLL_INTERVAL)

    async def _fetch_all(self, client: httpx.AsyncClient):
        updates = {}
        for key, path in _ENDPOINTS.items():
            try:
                resp = await client.get(
                    f"{_BASE}{path}",
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
