"""
reset_state.py — Wipe all logs, counters, and runtime metrics for a clean start.

Clears:
  logs/          — all application log files and rotated backups
  bot.log        — root-level combined log
  fills.jsonl    — calibration signal log
  fills_markout.jsonl — markout tracker data
  maker_data/maker_checkpoint.json  — live inventory, P&L, session state
  maker_data/maker_lifetime.json    — cumulative stats across all days
  maker_data/maker_fills/           — per-day fill ledgers

Does NOT touch:
  .env, entities.json, .claude/, or any source code
"""

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).parent

FILES = [
    ROOT / "bot.log",
    ROOT / "fills.jsonl",
    ROOT / "fills_markout.jsonl",
]

DIRS_TO_CLEAR = [
    ROOT / "logs",
]

MAKER_DATA = ROOT / "maker_data"
MAKER_CHECKPOINT = MAKER_DATA / "maker_checkpoint.json"
MAKER_LIFETIME = MAKER_DATA / "maker_lifetime.json"
MAKER_FILLS_DIR = MAKER_DATA / "maker_fills"

EMPTY_CHECKPOINT = {
    "saved_at": 0,
    "session_id": "",
    "inventory": {},
    "cash_pnl": 0.0,
    "realized_pnl": 0.0,
    "total_fills": 0,
    "total_cancels": 0,
    "fill_history": [],
    "open_lots": {},
    "daily_fills_seen": [],
    "session_by_market": {},
    "today_stats": {},
    "lifetime_stats": {},
    "selected_token_ids": [],
    "reduce_only_markets": [],
    "cooldowns": {},
    "global_cooldown_until": 0.0,
    "last_fill_times": {},
    "recent_fill_sides": {},
}

EMPTY_LIFETIME = {
    "last_date": None,
    "total_fills": 0,
    "total_realized_pnl": 0.0,
    "total_cash_pnl": 0.0,
    "by_date": {},
}


def _size(path: Path) -> str:
    if path.is_file():
        b = path.stat().st_size
        return f"{b / 1024:.1f} KB" if b >= 1024 else f"{b} B"
    if path.is_dir():
        total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
        count = sum(1 for f in path.rglob("*") if f.is_file())
        return f"{count} files, {total / 1024:.1f} KB"
    return "not found"


def preview() -> None:
    print("\n=== What will be cleared ===\n")
    for f in FILES:
        print(f"  DELETE  {f.relative_to(ROOT)}  ({_size(f)})")
    for d in DIRS_TO_CLEAR:
        print(f"  CLEAR   {d.relative_to(ROOT)}/  ({_size(d)})")
    print(f"  RESET   {MAKER_CHECKPOINT.relative_to(ROOT)}  ({_size(MAKER_CHECKPOINT)})")
    print(f"  RESET   {MAKER_LIFETIME.relative_to(ROOT)}  ({_size(MAKER_LIFETIME)})")
    if MAKER_FILLS_DIR.exists():
        print(f"  CLEAR   {MAKER_FILLS_DIR.relative_to(ROOT)}/  ({_size(MAKER_FILLS_DIR)})")
    print()


def run() -> None:
    # Delete individual files
    for f in FILES:
        if f.exists():
            f.unlink()
            print(f"  deleted  {f.name}")

    # Clear log directories (recreate empty)
    for d in DIRS_TO_CLEAR:
        if d.exists():
            shutil.rmtree(d)
            d.mkdir()
            print(f"  cleared  {d.name}/")

    # Reset checkpoint to empty struct (not deleted — bot expects the file)
    MAKER_DATA.mkdir(exist_ok=True)
    MAKER_CHECKPOINT.write_text(json.dumps(EMPTY_CHECKPOINT, indent=2))
    print(f"  reset    {MAKER_CHECKPOINT.name}")

    # Reset lifetime stats
    MAKER_LIFETIME.write_text(json.dumps(EMPTY_LIFETIME, indent=2))
    print(f"  reset    {MAKER_LIFETIME.name}")

    # Clear per-day fill ledgers
    if MAKER_FILLS_DIR.exists():
        shutil.rmtree(MAKER_FILLS_DIR)
        MAKER_FILLS_DIR.mkdir()
        print(f"  cleared  maker_fills/")

    print("\nDone. Start the bot fresh with: python main.py\n")


if __name__ == "__main__":
    preview()
    if "--yes" in sys.argv or "-y" in sys.argv:
        confirmed = True
    else:
        answer = input("Proceed? [y/N] ").strip().lower()
        confirmed = answer == "y"

    if confirmed:
        run()
    else:
        print("Aborted.")
