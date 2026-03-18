"""
trading/executor.py

v2.1 Fix 1: place_order accepts both yes_token_id and no_token_id.
BUY_NO routes to the NO token — py-clob-client handles YES and NO tokens identically.
Paper mode is the default. Set paper=False only after paper validation checklist passes.
"""

from dataclasses import dataclass
from typing import Optional
from config import POLYMARKET_CLOB_URL, POLY_PRIVATE_KEY
from utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class OrderResult:
    order_id: Optional[str]
    status: str
    filled_price: float
    filled_size: float
    token_used: str = ""
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.order_id is not None and self.status != "ERROR"


class CLOBExecutor:
    """
    Places orders via Polymarket CLOB API.
    paper=True (default): logs intent, returns synthetic fill. No network calls.
    paper=False: requires POLY_PRIVATE_KEY in .env. Only enable after paper validation.
    """

    def __init__(self, private_key: str = POLY_PRIVATE_KEY, paper: bool = True):
        self._paper = paper
        self._private_key = private_key
        if not paper:
            from py_clob_client.client import ClobClient  # type: ignore
            self._clob = ClobClient(
                host=POLYMARKET_CLOB_URL,
                key=private_key,
                chain_id=137,  # Polygon mainnet
            )

    async def place_order(
        self,
        yes_token_id: str,
        no_token_id: str,
        side: str,             # "BUY_YES" | "BUY_NO"
        size: float,
        price: float,
    ) -> OrderResult:
        if side not in ("BUY_YES", "BUY_NO"):
            raise ValueError(f"side must be BUY_YES or BUY_NO, got {side!r}")
        if size <= 0:
            raise ValueError(f"size must be > 0, got {size}")

        # v2.1 Fix 1: route to the correct token based on side
        actual_token = yes_token_id if side == "BUY_YES" else no_token_id
        clob_side = "BUY"  # py-clob-client always receives "BUY" for the chosen token

        if self._paper:
            log.info(
                f"PAPER | {side} token={actual_token[:8]} "
                f"size=${size:.2f} price={price:.4f}"
            )
            return OrderResult(
                order_id="PAPER",
                status="PAPER",
                filled_price=price,
                filled_size=size,
                token_used=actual_token,
            )

        try:
            resp = self._clob.create_and_post_order(
                self._clob.create_order(
                    token_id=actual_token,
                    price=price,
                    size=size,
                    side=clob_side,
                )
            )
            order_id = resp.get("orderID")
            log.info(f"ORDER | id={order_id} {side} size=${size:.2f} price={price:.4f}")
            return OrderResult(
                order_id=order_id,
                status=resp.get("status", "UNKNOWN"),
                filled_price=float(resp.get("price", price)),
                filled_size=float(resp.get("size", size)),
                token_used=actual_token,
            )
        except Exception as exc:
            log.error(f"order failed: {exc}")
            return OrderResult(
                order_id=None, status="ERROR",
                filled_price=0.0, filled_size=0.0,
                error=str(exc),
            )

    async def cancel_order(self, order_id: str) -> bool:
        if self._paper:
            return True
        try:
            self._clob.cancel(order_id)
            return True
        except Exception as exc:
            log.error(f"cancel {order_id} failed: {exc}")
            return False
