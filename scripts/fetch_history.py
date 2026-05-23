"""
scripts/fetch_history.py — Fetch trade history and open positions from Polymarket.

Usage:
    python scripts/fetch_history.py
    python scripts/fetch_history.py --redeem   # also trigger redemption for eligible positions
"""
import argparse
import asyncio
import functools
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv

load_dotenv()

_DATA_API = "https://data-api.polymarket.com"
_CLOB_API = "https://clob.polymarket.com"

WALLET = (
    os.getenv("FUNDER_ADDRESS")
    or os.getenv("POLY_PROXY_ADDRESS")
    or ""
)


def _fmt_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _sign(x: float) -> str:
    return f"+{x:.4f}" if x >= 0 else f"{x:.4f}"


async def fetch_positions(client: httpx.AsyncClient) -> list[dict]:
    r = await client.get(f"{_DATA_API}/positions", params={"user": WALLET})
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []


async def fetch_activity(client: httpx.AsyncClient, limit: int = 500) -> list[dict]:
    r = await client.get(f"{_DATA_API}/activity", params={"user": WALLET, "limit": limit})
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []


async def main(do_redeem: bool = False) -> None:
    if not WALLET:
        sys.exit("ERROR: FUNDER_ADDRESS not set in .env")

    async with httpx.AsyncClient(timeout=20.0) as client:
        positions, activity = await asyncio.gather(
            fetch_positions(client),
            fetch_activity(client),
        )

    # -- Summary header -------------------------------------------------------
    print(f"\n{'='*72}")
    print(f"  Polymarket History - {WALLET[:10]}...{WALLET[-6:]}")
    print(f"  Fetched at {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*72}\n")

    # -- Activity grouped by market --------------------------------------------
    by_market: dict[str, dict] = defaultdict(lambda: {
        "title": "", "outcome": "", "trades": [],
        "bought": 0.0, "sold": 0.0,
        "usdc_spent": 0.0, "usdc_received": 0.0,
    })

    trades = [a for a in activity if a.get("type") == "TRADE"]
    for t in sorted(trades, key=lambda x: x["timestamp"]):
        key = t.get("asset", t.get("conditionId", ""))
        m = by_market[key]
        m["title"] = t.get("title", "")
        m["outcome"] = t.get("outcome", "")
        side = t.get("side", "")
        size = float(t.get("size", 0))
        usdc = float(t.get("usdcSize", 0))
        price = float(t.get("price", 0))
        m["trades"].append({
            "ts": t["timestamp"], "side": side,
            "size": size, "price": price, "usdc": usdc,
        })
        if side == "BUY":
            m["bought"] += size
            m["usdc_spent"] += usdc
        else:
            m["sold"] += size
            m["usdc_received"] += usdc

    print(f"TRADE HISTORY  ({len(trades)} trades across {len(by_market)} markets)")
    print(f"{'-'*72}")
    for asset, m in by_market.items():
        title = m["title"][:60] if m["title"] else asset[:20]
        outcome = m.get("outcome", "")
        net = m["bought"] - m["sold"]
        cash = m["usdc_received"] - m["usdc_spent"]
        print(f"\n  {title}")
        if outcome:
            print(f"  Outcome: {outcome}")
        print(f"  {'Side':5} {'Shares':>8} {'Price':>7} {'USDC':>8}  {'Time (UTC)'}")
        print(f"  {'-'*62}")
        for tr in m["trades"]:
            print(
                f"  {tr['side']:5} {tr['size']:>8.1f} {tr['price']:>7.4f}"
                f" {tr['usdc']:>8.2f}  {_fmt_ts(tr['ts'])}"
            )
        print(f"  {'-'*62}")
        print(
            f"  Net position: {net:>+8.1f} sh  |  "
            f"Cash flow: {_sign(cash)} USDC  "
            f"(spent ${m['usdc_spent']:.2f}, received ${m['usdc_received']:.2f})"
        )

    # -- Open positions --------------------------------------------------------
    print(f"\n\n{'-'*72}")
    print(f"OPEN POSITIONS  ({len(positions)} positions)")
    print(f"{'-'*72}")

    total_initial = 0.0
    total_current = 0.0
    redeemable_positions = []

    for p in positions:
        title = (p.get("title") or "")[:60]
        outcome = p.get("outcome", "")
        size = float(p.get("size", 0))
        avg_price = float(p.get("avgPrice", 0))
        cur_price = float(p.get("curPrice", 0))
        initial_val = float(p.get("initialValue", 0))
        current_val = float(p.get("currentValue", 0))
        cash_pnl = float(p.get("cashPnl", 0))
        pct_pnl = float(p.get("percentPnl", 0))
        redeemable = p.get("redeemable", False)
        mergeable = p.get("mergeable", False)
        end_date = p.get("endDate", "")

        total_initial += initial_val
        total_current += current_val

        status = []
        if redeemable:
            status.append("REDEEMABLE (run --redeem)")
            redeemable_positions.append(p)
        if mergeable:
            status.append("MERGEABLE")
        status_str = " | ".join(status) if status else "active"

        print(f"\n  {title}")
        print(f"  Outcome: {outcome}  |  End: {end_date}  |  [{status_str}]")
        print(
            f"  {size:.1f} sh @ avg {avg_price:.4f}  |  "
            f"Cur: {cur_price:.4f}  |  "
            f"Value: ${current_val:.2f} (invested ${initial_val:.2f})"
        )
        print(f"  Cash P&L: {_sign(cash_pnl)} USDC  ({pct_pnl:+.1f}%)")

    unrealized = total_current - total_initial
    print(f"\n{'-'*72}")
    print(
        f"  TOTAL  invested: ${total_initial:.2f}  "
        f"current: ${total_current:.2f}  "
        f"unrealized: {_sign(unrealized)} USDC"
    )
    print(f"{'-'*72}\n")

    # -- Redemption ------------------------------------------------------------
    if do_redeem and redeemable_positions:
        from trading.redeem import redeem_position
        rpc_url = os.getenv("RPC_URL", "https://polygon-rpc.com")
        private_key = os.getenv("POLY_PRIVATE_KEY", "")
        if not private_key:
            print("  ERROR: POLY_PRIVATE_KEY not set — cannot redeem")
        else:
            print("  Redeeming eligible positions...")
            for p in redeemable_positions:
                cid = p.get("conditionId", "")
                title = (p.get("title") or "")[:55]
                ok = await asyncio.get_event_loop().run_in_executor(
                    None, functools.partial(redeem_position, cid, rpc_url, private_key)
                )
                mark = "OK" if ok else "FAILED"
                print(f"    [{mark}] {title}")
                await asyncio.sleep(2)
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch Polymarket trade history and positions")
    parser.add_argument("--redeem", action="store_true", help="Redeem eligible resolved positions")
    args = parser.parse_args()
    asyncio.run(main(do_redeem=args.redeem))
