"""
scripts/dedup_fills.py — Deduplicate fills.jsonl.

For each (token_id, side) pair, keep only the chronologically first entry.
Duplicate entries were created by the position-re-entry bug (expire without
ledger persistence → reload as open → re-enter same market).

Usage:
    python -m scripts.dedup_fills                    # dry-run, prints stats
    python -m scripts.dedup_fills --apply            # write deduplicated file
    python -m scripts.dedup_fills --apply --archive  # also archive duplicates
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import config

FILLS_PATH = Path(config.TRACKED_POSITIONS_FILE).parent / "fills.jsonl"
ARCHIVE_PATH = Path("fills.jsonl.dedup_archive")


def run(apply: bool = False, archive: bool = False) -> None:
    if not FILLS_PATH.exists():
        print(f"fills.jsonl not found at {FILLS_PATH}")
        return

    lines = FILLS_PATH.read_text(encoding="utf-8").splitlines()
    records = []
    parse_errors = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            parse_errors += 1

    seen: dict[tuple, int] = {}   # (token_id, side) → index of first occurrence
    kept: list[dict] = []
    dupes: list[dict] = []

    for rec in records:
        key = (rec.get("token_id", ""), rec.get("side", ""))
        if key not in seen:
            seen[key] = len(kept)
            kept.append(rec)
        else:
            dupes.append(rec)

    total  = len(records)
    unique = len(kept)
    n_dupe = len(dupes)

    print(f"\nfills.jsonl deduplication")
    print(f"  Total records  : {total}")
    print(f"  Unique (kept)  : {unique}")
    print(f"  Duplicates     : {n_dupe}")
    print(f"  Parse errors   : {parse_errors}")

    if not apply:
        print("\nDry-run — no files changed. Pass --apply to write.")
        return

    # Backup original
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup = FILLS_PATH.with_suffix(f".jsonl.bak.{ts}")
    shutil.copy2(FILLS_PATH, backup)
    print(f"\nBackup written: {backup}")

    # Write deduplicated file
    FILLS_PATH.write_text(
        "\n".join(json.dumps(r) for r in kept) + "\n",
        encoding="utf-8",
    )
    print(f"Deduplicated fills.jsonl written ({unique} records)")

    if archive and dupes:
        ARCHIVE_PATH.write_text(
            "\n".join(json.dumps(r) for r in dupes) + "\n",
            encoding="utf-8",
        )
        print(f"Archive written: {ARCHIVE_PATH} ({n_dupe} duplicate records)")


if __name__ == "__main__":
    run(
        apply="--apply" in sys.argv,
        archive="--archive" in sys.argv,
    )
