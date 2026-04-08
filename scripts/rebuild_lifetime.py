#!/usr/bin/env python3
"""Rebuild maker_lifetime.json from all fill ledger files.

Run this if maker_lifetime.json is missing, corrupted, or out of sync with
the fill ledger (e.g., after manually editing fill files, or after migrating
from a previous version).

Usage:
    python scripts/rebuild_lifetime.py
    python scripts/rebuild_lifetime.py --base maker_data
"""

import argparse
import sys
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from maker.fill_ledger import FillLedger
from maker.state_persistence import LifetimeStatsCache


def main():
    parser = argparse.ArgumentParser(description="Rebuild maker lifetime stats cache")
    parser.add_argument("--base", default="maker_data", help="maker_data base directory")
    args = parser.parse_args()

    base = Path(args.base)
    ledger = FillLedger(base_dir=base)
    cache = LifetimeStatsCache(base_dir=base)

    all_dates = ledger.all_dates()
    if not all_dates:
        print("No fill ledger files found — nothing to rebuild.")
        return

    print(f"Found fill ledger dates: {all_dates}")
    stats = cache.rebuild(ledger)
    print(
        f"Rebuilt maker_lifetime.json:\n"
        f"  last_date={stats['last_date']}\n"
        f"  total_fills={stats['total_fills']}\n"
        f"  total_cash_pnl={stats['total_cash_pnl']:.4f}\n"
        f"  total_realized_pnl={stats['total_realized_pnl']:.4f}\n"
        f"  markets tracked={len(stats['by_market'])}\n"
        f"  days tracked={len(stats['by_date'])}"
    )


if __name__ == "__main__":
    main()
