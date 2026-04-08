"""Append-only daily fill ledger for the maker bot."""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path


class FillLedger:
    """Writes one JSON line per fill to maker_data/maker_fills/maker_fills_YYYY-MM-DD.jsonl.

    Handles UTC midnight rollover automatically — each append() checks the date
    and opens a new file if needed. Thread-safe for single-writer use (asyncio).
    """

    def __init__(self, base_dir: str | Path = "maker_data"):
        self._base = Path(base_dir)
        self._fills_dir = self._base / "maker_fills"
        self._fills_dir.mkdir(parents=True, exist_ok=True)
        self._current_date: str = ""   # UTC date string "YYYY-MM-DD"
        self._fh = None                # open file handle

    def _date_for_ts(self, ts: float) -> str:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")

    def _filepath(self, date_str: str) -> Path:
        return self._fills_dir / f"maker_fills_{date_str}.jsonl"

    def _ensure_open(self, ts: float) -> None:
        date_str = self._date_for_ts(ts)
        if date_str != self._current_date:
            if self._fh is not None:
                self._fh.close()
            self._current_date = date_str
            self._fh = open(self._filepath(date_str), "a", encoding="utf-8")

    def append(
        self,
        session_id: str,
        token_id: str,
        question: str,
        side: str,
        price: float,
        size: float,
        cash_flow: float,
        realized_pnl: float,
        inventory_after: float,
        filled_at: float,
    ) -> str:
        """Append one fill record. Returns the generated fill_id."""
        self._ensure_open(filled_at)
        ts_int = int(filled_at * 1000)  # millisecond precision for uniqueness
        fill_id = f"{token_id[:8]}-{side.lower()}-{price:.4f}-{ts_int}"
        record = {
            "fill_id": fill_id,
            "session_id": session_id,
            "ts": filled_at,
            "token_id": token_id,
            "question": question,
            "side": side,
            "price": price,
            "size": size,
            "cash_flow": cash_flow,
            "realized_pnl": realized_pnl,
            "inventory_after": inventory_after,
        }
        self._fh.write(json.dumps(record) + "\n")
        self._fh.flush()
        return fill_id

    def read_date(self, date_str: str | None = None) -> list[dict]:
        """Read all fill records for a given UTC date (defaults to today)."""
        if date_str is None:
            date_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        path = self._filepath(date_str)
        if not path.exists():
            return []
        records = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return records

    def all_dates(self) -> list[str]:
        """Return sorted list of all date strings with fill data."""
        dates = []
        for p in self._fills_dir.glob("maker_fills_*.jsonl"):
            name = p.stem  # "maker_fills_YYYY-MM-DD"
            parts = name.split("_")
            if len(parts) >= 3:
                dates.append(parts[-1])  # last part is "YYYY-MM-DD"
        return sorted(dates)

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def __del__(self) -> None:
        self.close()
