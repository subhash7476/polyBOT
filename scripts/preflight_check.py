#!/usr/bin/env python3
"""
scripts/preflight_check.py — Paper validation gate before going live.

Usage:
    python scripts/preflight_check.py [--fills fills.jsonl] [--bankroll 500]

Exit code 0 = all checks pass (ready for live).
Exit code 1 = one or more checks fail.
"""
import argparse
import sys
import math
from pathlib import Path

# Ensure project root is on sys.path when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from calibration.metrics import load_resolved_fills, paper_validation_report


def main():
    parser = argparse.ArgumentParser(description="Paper validation preflight check")
    parser.add_argument("--fills", default="fills.jsonl")
    parser.add_argument("--bankroll", type=float, default=500.0)
    args = parser.parse_args()

    # load ALL fills (not just resolved) for total signal count
    from pathlib import Path
    import json
    all_fills = []
    path = Path(args.fills)
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line:
                all_fills.append(json.loads(line))

    report = paper_validation_report(all_fills, bankroll=args.bankroll)

    print("\n" + "=" * 55)
    print("  PAPER VALIDATION PREFLIGHT REPORT")
    print("=" * 55)

    label_map = {
        "signals_50":       "50+ total signals",
        "resolved_20":      "20+ resolved outcomes",
        "brier_lt_020":     "Brier score < 0.20",
        "mean_edge_gt_003": "Mean edge > 3%",
        "no_bad_day":       "No day > 5% loss",
    }
    value_map = {
        "signals_50":       lambda c: f"{c['value']} signals",
        "resolved_20":      lambda c: f"{c['value']} resolved",
        "brier_lt_020":     lambda c: f"brier={c['value']}" if c['value'] is not None else "brier=n/a",
        "mean_edge_gt_003": lambda c: f"edge={c['value']:.2%}",
        "no_bad_day":       lambda c: f"worst_day=${c['worst_day_loss']:.2f} (limit=${c['limit']:.2f})",
    }

    for key, check in report["checks"].items():
        status = "PASS" if check["pass"] else "FAIL"
        label = label_map[key]
        value = value_map[key](check)
        print(f"  [{status}] {label:<30} {value}")

    print("=" * 55)
    if report["ready"]:
        print("  READY TO GO LIVE — set PAPER=false in .env")
    else:
        failed = sum(1 for c in report["checks"].values() if not c["pass"])
        print(f"  NOT READY — {failed} check(s) failing")
    print("=" * 55 + "\n")

    sys.exit(0 if report["ready"] else 1)


if __name__ == "__main__":
    main()
