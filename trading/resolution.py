from __future__ import annotations

import asyncio
from typing import Any

import httpx

import config
from calibration.tracker import CalibrationTracker
from trading.redeemall import extract_wallet_token_id, fetch_positions
from trading.risk import RiskManager
from utils.logger import get_logger

log = get_logger(__name__)

_GAMMA_URL = "https://gamma-api.polymarket.com/markets"


def _normalize_winner(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
    if isinstance(value, str):
        raw = value.strip().lower()
        if raw in {"yes", "true", "winner_yes", "outcome_yes"}:
            return True
        if raw in {"no", "false", "winner_no", "outcome_no"}:
            return False
    return None


def _resolved_yes_from_market_payload(payload: dict) -> bool | None:
    for key in ("winningOutcome", "winner", "result", "resolution"):
        normalized = _normalize_winner(payload.get(key))
        if normalized is not None:
            return normalized

    market = payload.get("market", {})
    for key in ("winningOutcome", "winner", "result", "resolution"):
        normalized = _normalize_winner(market.get(key))
        if normalized is not None:
            return normalized

    for source in (payload, market):
        prices = source.get("outcomePrices")
        if isinstance(prices, str):
            try:
                import json
                prices = json.loads(prices)
            except Exception:
                prices = None
        if isinstance(prices, list) and len(prices) >= 2:
            try:
                yes_price = float(prices[0])
                no_price = float(prices[1])
            except (TypeError, ValueError):
                continue
            if yes_price >= 0.999 and no_price <= 0.001:
                return True
            if no_price >= 0.999 and yes_price <= 0.001:
                return False
    return None


async def fetch_market_resolution(client: httpx.AsyncClient, token_id: str) -> tuple[bool, bool | None, dict]:
    """Return (is_resolved, resolved_yes, raw_payload) for a token id."""
    candidate_params = (
        {"clob_token_ids": token_id},
        {"clobTokenIds": token_id},
        {"id": token_id},
    )
    payload: dict | None = None
    for params in candidate_params:
        resp = await client.get(_GAMMA_URL, params=params, timeout=15.0)
        if resp.status_code == 422:
            # Gamma rejects the token format — not a real market token, skip entirely
            return False, None, {}
        resp.raise_for_status()
        data = resp.json()
        items = data if isinstance(data, list) else data.get("data", [])
        if items:
            payload = items[0]
            break

    if not payload:
        return False, None, {}

    market = payload.get("market", {})
    is_resolved = bool(payload.get("resolved") or market.get("resolved"))
    resolved_yes = _resolved_yes_from_market_payload(payload) if is_resolved else None
    return is_resolved, resolved_yes, payload


async def reconcile_wallet_positions(wallet_address: str, risk: RiskManager) -> list[str]:
    if not wallet_address:
        return []
    try:
        wallet_positions = await fetch_positions(wallet_address)
    except Exception as exc:
        log.warning(f"wallet reconciliation failed: {exc}")
        return []

    wallet_token_ids = {extract_wallet_token_id(p) for p in wallet_positions}
    wallet_token_ids.discard("")
    for wallet_pos in wallet_positions:
        token_id = extract_wallet_token_id(wallet_pos)
        condition_id = wallet_pos.get("conditionId") or wallet_pos.get("condition_id", "")
        if token_id and condition_id:
            await risk.update_wallet_metadata(token_id, condition_id=condition_id)
    mismatches = []
    async with risk._lock:
        tracked_ids = set(risk.open_positions) | set(risk.pending_redemptions)
    for token_id in sorted(tracked_ids - wallet_token_ids):
        mismatches.append(token_id)
        log.warning(f"tracked position missing from wallet snapshot: {token_id[:12]}")
    return mismatches


async def process_resolutions(
    risk: RiskManager,
    tracker: CalibrationTracker,
    *,
    paper: bool,
    wallet_address: str = "",
) -> dict[str, int]:
    resolved_count = 0
    unresolved_count = 0
    resolved_errors = 0

    async with risk._lock:
        tracked_ids = list(risk.open_positions.keys())

    if not tracked_ids:
        return {"resolved": 0, "still_open": 0, "errors": 0}

    async with httpx.AsyncClient() as client:
        for token_id in tracked_ids:
            try:
                is_resolved, resolved_yes, payload = await fetch_market_resolution(client, token_id)
                # Opportunistically backfill condition_id from Gamma API response
                # (paper positions have no CLOB response to extract it from at trade time)
                cid = payload.get("conditionId", "") or ""
                if cid:
                    await risk.update_wallet_metadata(token_id, condition_id=cid)
                if not is_resolved or resolved_yes is None:
                    unresolved_count += 1
                    continue
                position = await risk.resolve_position(token_id, resolved_yes, paper=paper)
                if position is None:
                    continue
                tracker.record_outcome(token_id, resolved_yes)
                resolved_count += 1
            except Exception as exc:
                resolved_errors += 1
                log.warning(f"resolution check failed for {token_id[:12]}: {exc}")

    if not paper and wallet_address:
        await reconcile_wallet_positions(wallet_address, risk)

    return {
        "resolved": resolved_count,
        "still_open": unresolved_count,
        "errors": resolved_errors,
    }


async def resolution_loop(
    risk: RiskManager,
    tracker: CalibrationTracker,
    *,
    paper: bool,
    wallet_address: str = "",
    interval: int = config.RESOLUTION_POLL_INTERVAL_SECONDS,
):
    heartbeat_count = 0
    await asyncio.sleep(15)
    while True:
        stats = await process_resolutions(
            risk,
            tracker,
            paper=paper,
            wallet_address=wallet_address,
        )
        heartbeat_count += 1
        async with risk._lock:
            tracked_open = len(risk.open_positions)
            tracked_pending = len(risk.pending_redemptions)
        if stats["resolved"] or stats["errors"]:
            log.info(
                f"resolution loop: resolved={stats['resolved']} "
                f"open={stats['still_open']} errors={stats['errors']}"
            )
        elif (tracked_open or tracked_pending) and heartbeat_count % 5 == 0:
            log.info(
                f"resolution heartbeat: tracked_open={tracked_open} "
                f"pending_redeem={tracked_pending}"
            )
        await asyncio.sleep(interval)
