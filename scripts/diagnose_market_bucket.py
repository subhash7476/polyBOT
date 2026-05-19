"""Diagnose why a market category does or does not reach maker selection.

This is intentionally read-only. It fetches the same active Gamma market universe
used by the maker bot and applies the MarketSelector gates in the same order, then
prints failure counts and representative markets.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from market.clob_monitor import fetch_active_markets  # noqa: E402
from maker import market_selector as selector  # noqa: E402


@dataclass(frozen=True)
class GateResult:
    reason: str
    days_left: float | None = None
    spread: float = 0.0
    volume: float = 0.0


def _parse_end_ts(expiry) -> float | None:
    if expiry is None:
        return None
    if hasattr(expiry, "timestamp"):
        return float(expiry.timestamp())
    try:
        return float(expiry)
    except (TypeError, ValueError):
        return None


def classify_gate(meta: dict, *, max_days: float, require_end_date: bool) -> GateResult:
    category = str(meta.get("category") or "unknown").lower()
    spread = float(meta.get("best_ask", 1.0)) - float(meta.get("best_bid", 0.0))
    volume = float(meta.get("volume_24h") or meta.get("volume") or 0.0)
    end_ts = _parse_end_ts(meta.get("expiry"))
    days_left = (end_ts - time.time()) / 86400.0 if end_ts is not None else None

    if category in selector._EXCLUDED_CATEGORIES:
        return GateResult("excluded_cat", days_left=days_left, spread=spread, volume=volume)
    if spread < selector._MIN_SPREAD:
        return GateResult("tight_spread", days_left=days_left, spread=spread, volume=volume)
    if volume < selector._MIN_DAILY_VOLUME:
        return GateResult("low_vol", days_left=days_left, spread=spread, volume=volume)

    bid = float(meta.get("best_bid") or 0.0)
    if bid < selector._MIN_BID or bid > selector._MAX_BID:
        return GateResult("bad_bid", days_left=days_left, spread=spread, volume=volume)

    if end_ts is None:
        if require_end_date:
            return GateResult("no_date", spread=spread, volume=volume)
        return GateResult("passed", spread=spread, volume=volume)

    days_left = (end_ts - time.time()) / 86400.0
    if days_left > max_days:
        return GateResult("far_future", days_left=days_left, spread=spread, volume=volume)

    min_days = (
        selector._MIN_DAYS_TO_RESOLVE_WEATHER
        if category == "weather"
        else selector._MIN_DAYS_TO_RESOLVE
    )
    if days_left < min_days:
        return GateResult("too_soon", days_left=days_left, spread=spread, volume=volume)

    return GateResult("passed", days_left=days_left, spread=spread, volume=volume)


def _fmt_days(days: float | None) -> str:
    if days is None:
        return "?"
    return f"{days:.1f}d"


def _console_safe(value: str) -> str:
    return value.encode("ascii", errors="replace").decode("ascii")


def print_samples(rows: list[tuple[str, dict, GateResult]], *, limit: int) -> None:
    for token_id, meta, gate in rows[:limit]:
        expiry = meta.get("expiry")
        expiry_s = expiry.isoformat() if hasattr(expiry, "isoformat") else str(expiry or "")
        question = _console_safe((meta.get("question") or "").replace("\n", " "))
        line = (
            f"- {gate.reason:<12} spread={gate.spread:5.3f} "
            f"vol24h=${gate.volume:,.0f} bid={float(meta.get('best_bid') or 0):.3f} "
            f"days={_fmt_days(gate.days_left):>5} token={token_id[:8]} "
            f"expiry={expiry_s[:19]} q={question[:140]}"
        )
        print(line)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--category", default="unknown", help="Category to inspect, or 'all'.")
    ap.add_argument("--sample", type=int, default=20, help="Rows to print per sample section.")
    ap.add_argument(
        "--max-days",
        type=float,
        default=selector._MAX_DAYS_TO_RESOLVE,
        help="Resolve-window max days for the primary pass.",
    )
    ap.add_argument(
        "--compare-max-days",
        type=float,
        default=14.0,
        help="Second resolve-window max days to compare against.",
    )
    ap.add_argument(
        "--allow-missing-date",
        action="store_true",
        help="Treat markets without an end date as eligible for this diagnostic.",
    )
    args = ap.parse_args()

    print(f"cwd={ROOT}")
    print(
        "thresholds: "
        f"min_vol=${selector._MIN_DAILY_VOLUME:,.0f} "
        f"min_spread={selector._MIN_SPREAD:.3f} "
        f"bid=[{selector._MIN_BID:.2f},{selector._MAX_BID:.2f}] "
        f"max_days={args.max_days:g} "
        f"excluded={sorted(selector._EXCLUDED_CATEGORIES)} "
        f"require_end_date={not args.allow_missing_date and selector._REQUIRE_END_DATE}"
    )

    async with httpx.AsyncClient(timeout=30) as client:
        token_map = await fetch_active_markets(client)

    category_counts = Counter(str(m.get("category") or "unknown") for m in token_map.values())
    print(f"\nuniverse={len(token_map)} categories={dict(category_counts)}")

    category = args.category.lower()
    rows: list[tuple[str, dict, GateResult]] = []
    compare_counts: Counter[str] = Counter()
    counts: Counter[str] = Counter()
    by_reason: dict[str, list[tuple[str, dict, GateResult]]] = defaultdict(list)
    require_end_date = selector._REQUIRE_END_DATE and not args.allow_missing_date

    for token_id, meta in token_map.items():
        meta_cat = str(meta.get("category") or "unknown").lower()
        if category != "all" and meta_cat != category:
            continue

        gate = classify_gate(meta, max_days=args.max_days, require_end_date=require_end_date)
        compare_gate = classify_gate(
            meta,
            max_days=args.compare_max_days,
            require_end_date=require_end_date,
        )
        rows.append((token_id, meta, gate))
        counts[gate.reason] += 1
        compare_counts[compare_gate.reason] += 1
        by_reason[gate.reason].append((token_id, meta, gate))

    print(f"\ncategory={args.category} count={len(rows)}")
    print(f"reasons @ max_days={args.max_days:g}: {dict(counts)}")
    print(f"reasons @ max_days={args.compare_max_days:g}: {dict(compare_counts)}")

    passed = by_reason.get("passed", [])
    passed.sort(key=lambda r: -(r[2].spread * r[2].volume))
    print(f"\nTop passed by spread*volume ({len(passed)} total):")
    print_samples(passed, limit=args.sample)

    print("\nRepresentative failures:")
    for reason, reason_rows in sorted(by_reason.items(), key=lambda item: (-len(item[1]), item[0])):
        if reason == "passed":
            continue
        reason_rows.sort(key=lambda r: -(r[2].spread * r[2].volume))
        print(f"\n{reason} ({len(reason_rows)}):")
        print_samples(reason_rows, limit=min(args.sample, 10))

    unlocked = []
    for token_id, meta, gate in rows:
        if gate.reason == "passed":
            continue
        compare_gate = classify_gate(
            meta,
            max_days=args.compare_max_days,
            require_end_date=require_end_date,
        )
        if compare_gate.reason == "passed":
            unlocked.append((token_id, meta, compare_gate))
    unlocked.sort(key=lambda r: -(r[2].spread * r[2].volume))
    print(f"\nUnlocked by max_days {args.max_days:g}->{args.compare_max_days:g}: {len(unlocked)}")
    print_samples(unlocked, limit=args.sample)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
