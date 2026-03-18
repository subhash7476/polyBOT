import asyncio
import httpx
from market.state import AppState
from utils.logger import get_logger

log = get_logger(__name__)

_BINANCE_URL = "https://fapi.binance.com"
_POLL_INTERVAL = 60


class MicrostructureFeed:
    """Binance perp: funding rate + open interest for BTC."""

    def __init__(self, state: AppState):
        self._state = state
        self._prev_oi: float | None = None

    async def start(self):
        log.info("starting")
        async with httpx.AsyncClient(timeout=10.0) as client:
            while True:
                try:
                    await self._fetch_all(client)
                except Exception as exc:
                    log.warning(f"microstructure fetch error: {exc}")
                await asyncio.sleep(_POLL_INTERVAL)

    async def _fetch_all(self, client: httpx.AsyncClient):
        r1 = await client.get(f"{_BINANCE_URL}/fapi/v1/premiumIndex",
                               params={"symbol": "BTCUSDT"})
        r1.raise_for_status()
        fr = float(r1.json()["lastFundingRate"])

        r2 = await client.get(f"{_BINANCE_URL}/fapi/v1/openInterest",
                               params={"symbol": "BTCUSDT"})
        r2.raise_for_status()
        oi = float(r2.json()["openInterest"])

        oi_change = ((oi - self._prev_oi) / self._prev_oi) if self._prev_oi else 0.0
        self._prev_oi = oi

        await self._state.update_feeds(
            btc_funding_rate=fr,
            btc_open_interest=oi,
        )
        log.debug(f"funding={fr:.6f} oi={oi:.0f} oi_change={oi_change:.4f}")
