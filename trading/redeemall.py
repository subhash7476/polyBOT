"""
trading/redeemall.py — Batch redeem all eligible Polymarket positions.

Run manually:  python -m trading.redeemall
Called by bot: await run_redeemall(wallet, rpc_url, private_key)
"""
import asyncio
import functools
import os
import sys
import httpx
from trading.redeem import redeem_position
from trading.redeem_lock import RedeemLock
from utils.logger import get_logger

log = get_logger("redeemall")

_DATA_API = "https://data-api.polymarket.com"
_lock = RedeemLock()


def classify_positions(positions: list[dict]) -> tuple[list, list, list]:
    """
    Split positions into (redeemable, pending, active).

    redeemable: market closed AND resolved (oracle has spoken)
    pending:    market closed but NOT yet resolved
    active:     market still open
    """
    redeemable, pending, active = [], [], []
    for p in positions:
        mkt = p.get("market", {})
        if mkt.get("closed") and mkt.get("resolved"):
            redeemable.append(p)
        elif mkt.get("closed"):
            pending.append(p)
        else:
            active.append(p)
    return redeemable, pending, active


async def fetch_positions(wallet: str) -> list[dict]:
    """Fetch all positions for a wallet from the Polymarket Data API."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_DATA_API}/positions",
            params={"user": wallet},
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []


async def run_redeemall(wallet: str, rpc_url: str, private_key: str) -> int:
    """
    Fetch and redeem all eligible positions.
    Returns count of successful redemptions.
    """
    if not _lock.acquire():
        log.warning("redeemall already running — skipping")
        return 0

    redeemed = 0
    try:
        positions = await fetch_positions(wallet)
        redeemable, pending, active = classify_positions(positions)
        log.info(
            f"positions: {len(active)} active, "
            f"{len(pending)} pending, "
            f"{len(redeemable)} redeemable"
        )

        for pos in redeemable:
            condition_id = pos.get("conditionId") or pos.get("condition_id", "")
            if not condition_id:
                log.warning(f"position has no conditionId: {pos}")
                continue
            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(
                None,
                functools.partial(redeem_position, condition_id, rpc_url, private_key)
            )
            if success:
                redeemed += 1
            await asyncio.sleep(2)   # rate-limit between redemptions

    except Exception as exc:
        log.error(f"redeemall failed: {exc}")
    finally:
        _lock.release()

    log.info(f"redeemall complete: {redeemed} redeemed")
    return redeemed


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    wallet  = os.getenv("FUNDER_ADDRESS") or ""
    rpc     = os.getenv("RPC_URL", "https://polygon-rpc.com")
    key     = os.getenv("POLY_PRIVATE_KEY", "")
    if not key:
        sys.exit("POLY_PRIVATE_KEY not set")
    asyncio.run(run_redeemall(wallet, rpc, key))
