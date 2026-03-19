import asyncio
import httpx
import numpy as np
from market.state import AppState
from utils.logger import get_logger

log = get_logger(__name__)

_BINANCE_URL = "https://fapi.binance.com"
_POLL_INTERVAL = 60

BINANCE_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT",
    "BNBUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT",
]
SYMBOL_TO_ASSET = {s: s.replace("USDT", "") for s in BINANCE_SYMBOLS}


async def estimate_realized_vol(client: httpx.AsyncClient, symbol: str, days: int = 20) -> float:
    """
    Estimate annualized realized volatility from Binance daily klines.
    Used as DVOL proxy for assets without Deribit options (XRP, BNB, DOGE, ADA, AVAX).
    """
    try:
        r = await client.get(
            f"{_BINANCE_URL}/fapi/v1/klines",
            params={"symbol": symbol, "interval": "1d", "limit": days + 1},
        )
        r.raise_for_status()
        klines = r.json()
        closes = [float(k[4]) for k in klines]
        if len(closes) < 2:
            return 80.0
        log_returns = [np.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
        daily_vol = float(np.std(log_returns))
        annualized = daily_vol * np.sqrt(252) * 100  # as percentage
        return max(annualized, 10.0)  # floor at 10%
    except Exception as e:
        log.warning(f"realized vol fetch failed for {symbol}: {e}")
        return 80.0


# Assets that have Deribit DVOL — others use realized vol estimate
_DERIBIT_DVOL_ASSETS = {"BTC", "ETH", "SOL"}


class MicrostructureFeed:
    """Binance perp: funding rates + spot prices for all supported assets."""

    def __init__(self, state: AppState):
        self._state = state

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
        for symbol in BINANCE_SYMBOLS:
            asset = SYMBOL_TO_ASSET[symbol]
            try:
                r = await client.get(
                    f"{_BINANCE_URL}/fapi/v1/premiumIndex",
                    params={"symbol": symbol},
                )
                r.raise_for_status()
                d = r.json()
                spot = float(d["markPrice"])
                funding_rate = float(d["lastFundingRate"])
                await self._state.update_asset_feed(asset, spot=spot, funding_rate=funding_rate)
            except Exception as e:
                log.warning(f"funding rate fetch failed for {symbol}: {e}")

        # For assets without Deribit DVOL, estimate realized vol from Binance klines
        for symbol in BINANCE_SYMBOLS:
            asset = SYMBOL_TO_ASSET[symbol]
            if asset not in _DERIBIT_DVOL_ASSETS:
                vol = await estimate_realized_vol(client, symbol)
                await self._state.update_asset_feed(asset, dvol=vol)

        log.debug(f"microstructure updated for {len(BINANCE_SYMBOLS)} assets")
