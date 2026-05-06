"""
trading/executor.py

v2.1 Fix 1: place_order accepts both yes_token_id and no_token_id.
BUY_NO routes to the NO token — py-clob-client handles YES and NO tokens identically.
Paper mode is the default. Set paper=False only after paper validation checklist passes.
"""

import asyncio
import os
from dataclasses import dataclass
from typing import Optional
from eth_account import Account
from config import POLY_PRIVATE_KEY, SIGNATURE_TYPE, FUNDER_ADDRESS
from utils.logger import get_logger

_MAX_RETRIES = 3
_RETRY_DELAY = 1.0  # seconds between attempts

_ORDER_STALE_MINUTES = 30      # cancel open orders older than this
_PRICE_MOVE_CANCEL_PCT = 0.02  # cancel if price moved > 2% since order placed
_ORDER_SPLIT_THRESHOLD = 30.0  # split orders above this USDC amount

log = get_logger(__name__)


def should_cancel_stale_order(
    order_age_minutes: float,
    price_at_order: float,
    current_price: float,
) -> bool:
    """Return True if a stale open order should be cancelled and re-priced."""
    if order_age_minutes < _ORDER_STALE_MINUTES:
        return False
    price_change = abs(current_price - price_at_order) / max(price_at_order, 0.001)
    return price_change > _PRICE_MOVE_CANCEL_PCT


def split_order_sizes(total_size: float, n_splits: int = 2) -> list[float]:
    """
    For orders above _ORDER_SPLIT_THRESHOLD, split into n_splits pieces.
    Each piece gets a slightly different price (caller offsets by 0.5-1c).
    Returns list of sizes summing to total_size.
    """
    if total_size <= _ORDER_SPLIT_THRESHOLD:
        return [total_size]
    piece = round(total_size / n_splits, 2)
    sizes = [piece] * (n_splits - 1)
    sizes.append(round(total_size - sum(sizes), 2))
    return sizes


@dataclass
class OrderResult:
    order_id: Optional[str]
    status: str
    filled_price: float
    filled_size: float
    token_used: str = ""
    condition_id: str = ""
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

        # Derive wallet address based on signature type (read at init time so
        # tests that reload the module and monkeypatch env vars work correctly)
        sig_type = int(os.getenv("SIGNATURE_TYPE", str(SIGNATURE_TYPE)))
        funder = os.getenv("FUNDER_ADDRESS", FUNDER_ADDRESS)
        if not private_key:
            self.wallet_address = ""
        elif sig_type in (1, 2):
            self.wallet_address = funder
        else:
            self.wallet_address = Account.from_key(private_key).address

        if not paper:
            from trading.clob_factory import build_clob_client
            self._clob = build_clob_client(
                private_key=private_key,
                chain_id=137,
                sig_type=sig_type,
                funder=funder or None,
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

        last_exc = None
        for attempt in range(1, _MAX_RETRIES + 1):
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
                condition_id = resp.get("conditionId", "") or ""
                log.info(f"ORDER | id={order_id} {side} size=${size:.2f} price={price:.4f}")
                return OrderResult(
                    order_id=order_id,
                    status=resp.get("status", "UNKNOWN"),
                    filled_price=float(resp.get("price", price)),
                    filled_size=float(resp.get("size", size)),
                    token_used=actual_token,
                    condition_id=condition_id,
                )
            except Exception as exc:
                last_exc = exc
                log.warning(f"order attempt {attempt}/{_MAX_RETRIES} failed: {exc}")
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(_RETRY_DELAY)

        log.error(f"order failed after {_MAX_RETRIES} attempts: {last_exc}")
        return OrderResult(
            order_id=None, status="ERROR",
            filled_price=0.0, filled_size=0.0,
            error=str(last_exc),
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
