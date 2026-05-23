"""
trading/balance.py — USDC wallet balance polling.

Polls the Polymarket CLOB API (authenticated) every 60s for the live
available-collateral balance. Falls back to data-api.polymarket.com/value
(position value only) when no CLOB client is supplied.
"""
import asyncio
import time
import httpx
from market.state import AppState
from utils.logger import get_logger

log = get_logger("balance")

_DATA_API = "https://data-api.polymarket.com"
_DEFAULT_POLL_INTERVAL = 60.0

# USDC on Polygon uses 6 decimal places
_USDC_DECIMALS = 1_000_000


def _extract_balance_value(data) -> float:
    """Handle both legacy object and current list response shapes from /value."""
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
    def __init__(
        self,
        state: AppState,
        wallet_address: str,
        poll_interval: float = _DEFAULT_POLL_INTERVAL,
        clob=None,
    ):
        self._state = state
        self._wallet = wallet_address
        self._interval = poll_interval
        self._clob = clob

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
            log.info(f"USDC balance: ${balance:.2f} (cash delta: ${pnl:+.2f})")
        except Exception as exc:
            log.warning(f"balance fetch failed: {exc}")

    async def _fetch_usdc_balance(self, wallet: str) -> float:
        """Fetch available USDC collateral.

        Primary: CLOB get_balance_allowance(COLLATERAL) — returns the actual
        tradeable USDC held in the CTF Exchange attributed to this address.
        Balance field is in raw USDC units (6 decimals), so divide by 1e6.

        Fallback: data-api /value — returns open position value only, NOT
        including undeployed cash. Used only when no CLOB client is available.
        """
        if not wallet:
            return 0.0

        if self._clob is not None:
            try:
                return await self._fetch_via_clob()
            except Exception as exc:
                log.warning(f"CLOB balance fetch failed, falling back to data-api: {exc}")

        # Fallback: data-api /value (position value only — inaccurate for cash)
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{_DATA_API}/value",
                params={"user": wallet},
            )
            resp.raise_for_status()
            data = resp.json()
            val = _extract_balance_value(data)
            log.debug(f"data-api /value balance (positions only): ${val:.4f}")
            return val

    async def _fetch_via_clob(self) -> float:
        """Call CLOB get_balance_allowance in a thread (SDK is sync)."""
        from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType

        loop = asyncio.get_event_loop()
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        result = await loop.run_in_executor(
            None, self._clob.get_balance_allowance, params
        )
        raw = int(result.get("balance", 0))
        return raw / _USDC_DECIMALS
