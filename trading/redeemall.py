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
from trading.positions import TrackedPosition
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


def extract_wallet_token_id(position: dict) -> str:
    return (
        position.get("asset")
        or position.get("asset_id")
        or position.get("clobTokenId")
        or position.get("tokenId")
        or position.get("token_id")
        or ""
    )


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


async def redeem_tracked_positions(
    wallet: str,
    rpc_url: str,
    private_key: str,
    tracked_positions: dict[str, TrackedPosition],
) -> list[str]:
    """
    Redeem only tracked positions that are both wallet-visible and resolved.
    Returns the tracked token_ids that were successfully redeemed.
    """
    if not tracked_positions:
        return []
    if not _lock.acquire():
        log.warning("redeemall already running — skipping tracked redemption")
        return []

    redeemed_token_ids: list[str] = []
    try:
        positions = await fetch_positions(wallet)
        redeemable, _pending, _active = classify_positions(positions)
        tracked_by_token = tracked_positions

        for pos in redeemable:
            token_id = extract_wallet_token_id(pos)
            tracked = tracked_by_token.get(token_id)
            if not tracked:
                continue
            condition_id = pos.get("conditionId") or pos.get("condition_id", "") or tracked.condition_id
            if not condition_id:
                log.warning(f"tracked position {token_id[:12]} missing conditionId; cannot redeem")
                continue
            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(
                None,
                functools.partial(redeem_position, condition_id, rpc_url, private_key)
            )
            if success:
                redeemed_token_ids.append(token_id)
            await asyncio.sleep(2)
    except Exception as exc:
        log.error(f"tracked redemption failed: {exc}")
    finally:
        _lock.release()

    return redeemed_token_ids


async def redeemall_loop(
    paper: bool,
    wallet: str,
    rpc_url: str,
    private_key: str,
    tracked_positions_fn=None,
    mark_redeemed_fn=None,
    interval: int = 900,
) -> None:
    """
    Periodic redemption loop — shared by taker and maker modes.

    Args:
        paper: if True, loop sleeps without doing anything.
        wallet: on-chain wallet address.
        rpc_url: Polygon RPC endpoint.
        private_key: signing key.
        tracked_positions_fn: async callable () -> dict[str, TrackedPosition].
            If None, runs a full batch redeemall instead.
        mark_redeemed_fn: async callable (token_id: str) -> None.
            Called for each successfully redeemed position.
        interval: seconds between checks (default 900 = 15 min).
    """
    await asyncio.sleep(60)   # initial delay — let the bot warm up first
    while True:
        try:
            if paper:
                await asyncio.sleep(interval)
                continue

            if tracked_positions_fn is not None:
                pending = await tracked_positions_fn()
                redeemed_ids = await redeem_tracked_positions(
                    wallet=wallet,
                    rpc_url=rpc_url,
                    private_key=private_key,
                    tracked_positions=pending,
                )
                if mark_redeemed_fn:
                    for token_id in redeemed_ids:
                        await mark_redeemed_fn(token_id)
            else:
                await run_redeemall(wallet, rpc_url, private_key)

        except Exception as exc:
            log.error(f"redeemall_loop error: {exc}")

        await asyncio.sleep(interval)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    wallet  = os.getenv("FUNDER_ADDRESS") or ""
    rpc     = os.getenv("RPC_URL", "https://polygon-rpc.com")
    key     = os.getenv("POLY_PRIVATE_KEY", "")
    if not key:
        sys.exit("POLY_PRIVATE_KEY not set")
    asyncio.run(run_redeemall(wallet, rpc, key))
