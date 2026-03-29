"""
scripts/lifecycle_audit.py — Position lifecycle audit report.

Usage:
    python -m scripts.lifecycle_audit          # audit only
    python -m scripts.lifecycle_audit --fix    # also backfill condition_ids from Gamma API

Categories reported:
  LIVE        — open, condition_id present, held < 6h or held < 48h
  ZOMBIE      — open, held > PAPER_POSITION_TTL_HOURS, not yet expired from ledger
  STALE_CID   — open, condition_id empty (cannot be redeemed if live mode)
  PENDING     — resolved_pending_redeem (awaiting on-chain redemption)
  PENDING_CID — resolved_pending_redeem, condition_id empty (will block redeemall)
  UNRESOLVABLE — open, Gamma API returns no record (market may have been delisted)
"""
from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timezone

import httpx

import config
from trading.positions import PositionLedger, TrackedPosition, OPEN, RESOLVED_PENDING_REDEEM


_GAMMA_URL = "https://gamma-api.polymarket.com/markets"


def _ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _held(pos: TrackedPosition) -> str:
    h = (time.time() - pos.opened_at) / 3600
    return f"{h:.1f}h"


async def _fetch_gamma(client: httpx.AsyncClient, token_id: str) -> dict:
    for params in [{"clob_token_ids": token_id}, {"clobTokenIds": token_id}]:
        try:
            r = await client.get(_GAMMA_URL, params=params, timeout=15)
            if r.status_code == 422:
                return {}
            items = r.json() if isinstance(r.json(), list) else r.json().get("data", [])
            if items:
                return items[0]
        except Exception:
            pass
    return {}


async def audit(fix: bool = False) -> None:
    ledger = PositionLedger(config.TRACKED_POSITIONS_FILE)
    open_pos, pending = ledger.load_active()
    all_tracked = {**open_pos, **pending}

    now = time.time()
    ttl_sec = config.PAPER_POSITION_TTL_HOURS * 3600

    live: list[TrackedPosition] = []
    zombies: list[TrackedPosition] = []
    stale_cid: list[TrackedPosition] = []
    pending_ok: list[TrackedPosition] = []
    pending_no_cid: list[TrackedPosition] = []
    unresolvable: list[TrackedPosition] = []
    backfilled = 0

    async with httpx.AsyncClient() as client:
        for token_id, pos in all_tracked.items():
            payload = await _fetch_gamma(client, token_id)
            await asyncio.sleep(0.08)  # polite rate limit

            # Backfill condition_id if requested and missing
            if fix and not pos.condition_id:
                cid = payload.get("conditionId", "") or ""
                if cid:
                    pos.condition_id = cid
                    ledger.append(pos)
                    backfilled += 1

            if pos.status == RESOLVED_PENDING_REDEEM:
                if pos.condition_id:
                    pending_ok.append(pos)
                else:
                    pending_no_cid.append(pos)
                continue

            # OPEN positions
            if not payload:
                unresolvable.append(pos)
                continue

            is_resolved = bool(payload.get("resolved"))
            held_sec = now - pos.opened_at

            if is_resolved:
                # Still marked open in our ledger but market resolved — zombie
                zombies.append(pos)
            elif not pos.condition_id:
                stale_cid.append(pos)
            elif held_sec > ttl_sec:
                zombies.append(pos)
            else:
                live.append(pos)

    # ── Report ───────────────────────────────────────────────────────────────
    total = len(all_tracked)
    print(f"\nLifecycle Audit — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Ledger: {config.TRACKED_POSITIONS_FILE}  |  Total tracked: {total}")
    print("=" * 72)

    def _section(title: str, positions: list[TrackedPosition], warn: bool = False) -> None:
        tag = " ⚠" if warn and positions else ""
        print(f"\n{title} ({len(positions)}){tag}")
        for pos in sorted(positions, key=lambda p: p.opened_at):
            cid = pos.condition_id[:12] if pos.condition_id else "(no cid)"
            print(
                f"  {pos.token_id[:8]}  {_held(pos):>6}  {pos.category:<8}  "
                f"{cid:<14}  {pos.question[:44]}"
            )

    _section("LIVE — open, active, identifier present", live)
    _section("ZOMBIE — open but market resolved or TTL exceeded", zombies, warn=True)
    _section("STALE_CID — open, condition_id missing", stale_cid, warn=True)
    _section("PENDING — awaiting on-chain redemption", pending_ok)
    _section("PENDING_CID — pending but no condition_id (BLOCKS redeemall)", pending_no_cid, warn=True)
    _section("UNRESOLVABLE — Gamma API returns no record", unresolvable, warn=True)

    print("\n" + "=" * 72)
    print(f"Summary: live={len(live)}  zombie={len(zombies)}  stale_cid={len(stale_cid)}  "
          f"pending={len(pending_ok)}  pending_no_cid={len(pending_no_cid)}  "
          f"unresolvable={len(unresolvable)}")
    if fix and backfilled:
        print(f"Backfilled condition_id: {backfilled} positions updated in ledger")
    print()


if __name__ == "__main__":
    fix_mode = "--fix" in sys.argv
    asyncio.run(audit(fix=fix_mode))
