"""
scripts/reset_checkpoint.py

Rebuild maker_checkpoint.json cash_pnl from the Polymarket data-api.

The bot's in-process fill poller can miss fills when a market is deselected
(e.g. resolution sells after a market closes).  This leaves cash_pnl lower
than reality, which triggers the daily-loss-limit circuit breaker immediately
on restart.  This script fetches the authoritative activity from Polymarket,
recomputes the true total cash flow from all TRADE events, and patches the
checkpoint so the bot can restart cleanly.

Usage:
    python scripts/reset_checkpoint.py [--dry-run]
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

CHECKPOINT_PATH = Path("maker_data/maker_checkpoint.json")
DATA_API = "https://data-api.polymarket.com"


def _load_checkpoint() -> dict:
    if not CHECKPOINT_PATH.exists():
        sys.exit(f"ERROR: checkpoint not found at {CHECKPOINT_PATH}")
    with open(CHECKPOINT_PATH) as f:
        return json.load(f)


async def fetch_all_trades(client: httpx.AsyncClient, wallet: str, limit: int = 500) -> list[dict]:
    r = await client.get(f"{DATA_API}/activity", params={"user": wallet, "limit": limit})
    r.raise_for_status()
    data = r.json()
    return [a for a in data if a.get("type") == "TRADE"] if isinstance(data, list) else []


def compute_cash_flow(trades: list[dict]) -> tuple[float, list[dict]]:
    """
    Sum cash flows from all trades.
    Uses usdcSize field (authoritative USDC amount), signed by side.
    BUY = spent USDC (negative); SELL = received USDC (positive).
    """
    rows = []
    total = 0.0
    for t in sorted(trades, key=lambda x: x.get("timestamp", 0)):
        side = t.get("side", "")
        usdc = float(t.get("usdcSize", 0) or 0)
        price = float(t.get("price", 0) or 0)
        size = float(t.get("size", 0) or 0)
        ts = t.get("timestamp", 0)
        asset = t.get("asset", "")[:12]
        title = (t.get("title") or "")[:40]
        outcome = t.get("outcome", "")

        # Use usdcSize if present; fall back to price * size
        amount = usdc if usdc > 0 else price * size
        cf = -amount if side == "BUY" else +amount
        total += cf

        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%m-%d %H:%M")
        rows.append({
            "time": dt,
            "side": side,
            "asset": asset,
            "title": title,
            "outcome": outcome,
            "price": price,
            "size": size,
            "usdc": amount,
            "cash_flow": cf,
        })
    return total, rows


async def main(dry_run: bool) -> None:
    wallet = os.getenv("FUNDER_ADDRESS") or os.getenv("POLY_PROXY_ADDRESS") or ""
    if not wallet:
        sys.exit("ERROR: FUNDER_ADDRESS not set in .env")

    ckpt = _load_checkpoint()
    old_cash_pnl = float(ckpt.get("cash_pnl", 0.0))

    print(f"\nWallet : {wallet[:10]}...{wallet[-6:]}")
    print(f"Checkpoint: {CHECKPOINT_PATH}")
    print(f"Current checkpoint cash_pnl: ${old_cash_pnl:.4f}")
    print()

    print("Fetching trade activity from Polymarket API...")
    async with httpx.AsyncClient(timeout=20.0) as client:
        trades = await fetch_all_trades(client, wallet, limit=500)

    if not trades:
        print("No trades found in API response. Nothing to update.")
        return

    true_cash_pnl, rows = compute_cash_flow(trades)
    delta = true_cash_pnl - old_cash_pnl

    print(
        f"{'Time':<12} {'Side':<5} {'Title':<40} {'Outcome':<5}"
        f" {'Price':>7} {'Size':>7} {'USDC':>8} {'CashFlow':>10}"
    )
    print("-" * 102)
    for r in rows:
        print(
            f"{r['time']:<12} {r['side']:<5} {r['title']:<40} {r['outcome']:<5}"
            f" {r['price']:>7.4f} {r['size']:>7.1f} {r['usdc']:>8.2f} {r['cash_flow']:>+10.4f}"
        )
    print("-" * 102)
    print(f"{'API total cash_pnl':>82} {true_cash_pnl:>+10.4f}")
    print()
    print(f"Old checkpoint cash_pnl : ${old_cash_pnl:+.4f}")
    print(f"API-computed cash_pnl   : ${true_cash_pnl:+.4f}")
    print(f"Delta (missing from bot): ${delta:+.4f}")

    if abs(delta) < 0.001:
        print("\nCheckpoint is already accurate -- no update needed.")
        return

    if dry_run:
        print(f"\n[DRY RUN] Would set cash_pnl to ${true_cash_pnl:.4f}. Re-run without --dry-run to apply.")
        return

    # Warn if delta is suspiciously large
    if abs(delta) > 100:
        print(f"\nWARNING: delta ${delta:+.2f} is unusually large. Check the table above.")
        confirm = input("Proceed anyway? [y/N] ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            return

    ckpt["cash_pnl"] = round(true_cash_pnl, 8)
    with open(CHECKPOINT_PATH, "w") as f:
        json.dump(ckpt, f, indent=2)

    print(f"\nCheckpoint updated: cash_pnl set to ${true_cash_pnl:.4f}")
    print("The daily-loss-limit circuit breaker will not fire on the next restart.")
    print()
    print("Next steps:")
    print("  1. Verify the numbers above look correct")
    print("  2. python main.py   (restart the bot)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reset maker checkpoint cash_pnl from Polymarket API")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
