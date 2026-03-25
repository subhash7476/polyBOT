"""
trading/balance.py — USDC wallet balance polling.

Polls data-api.polymarket.com every 60s to track current USDC balance.
Sets session_start on first successful fetch.
"""
import asyncio
import time
import httpx
from market.state import AppState
from utils.logger import get_logger

log = get_logger("balance")

_DATA_API = "https://data-api.polymarket.com"
_DEFAULT_POLL_INTERVAL = 60.0


def _extract_balance_value(data) -> float:
    """Handle both legacy object and current list response shapes."""
    if isinstance(data, list):
        if not data:
            return 0.0
        first = data[0]
        if not isinstance(first, dict):
            raise TypeError(f"unexpected balance list item type: {type(first).__name__}")
        return float(first.get("value", 0))
    if isinstance(data, dict):
        if "portfolioValue" in data:
            return float(data.get("portfolioValue", 0))
        return float(data.get("value", 0))
    raise TypeError(f"unexpected balance response type: {type(data).__name__}")


class BalancePoller:
    def __init__(self, state: AppState, wallet_address: str, poll_interval: float = _DEFAULT_POLL_INTERVAL):
        self._state = state
        self._wallet = wallet_address
        self._interval = poll_interval

    async def start(self):
        log.info(f"balance poller starting for wallet {self._wallet[:10]}...")
        while True:
            await self._poll_once()
            await asyncio.sleep(self._interval)

    async def _poll_once(self):
        try:
            balance = await self._fetch_usdc_balance(self._wallet)
            async with self._state._lock:
                if self._state.balance.session_start == 0.0:
                    self._state.balance.session_start = balance
                self._state.balance.current = balance
                self._state.balance.last_update = time.time()
                pnl = self._state.balance.session_pnl  # read inside lock
            log.info(f"USDC balance: ${balance:.2f} (session P/L: ${pnl:+.2f})")
        except Exception as exc:
            log.warning(f"balance fetch failed: {exc}")

    async def _fetch_usdc_balance(self, wallet: str) -> float:
        """Fetch portfolio value in USDC from Polymarket Data API."""
        if not wallet:
            return 0.0
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{_DATA_API}/value",
                params={"user": wallet},
            )
            resp.raise_for_status()
            data = resp.json()
            return _extract_balance_value(data)
