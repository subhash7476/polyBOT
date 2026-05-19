# Maker State Persistence & Continuation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist maker bot inventory, P&L, and fills across restarts; add lifetime ledger with per-market breakdown; extend dashboard with session / today / all-time metrics.

**Architecture:** Three new files (`maker/fill_ledger.py`, `maker/state_persistence.py`, `scripts/rebuild_lifetime.py`) sit in the inventory/runner path. `FillLedger` appends every fill to daily JSONL files. `MakerStateLoader` restores from a 60s checkpoint on startup; `MakerCheckpointer` writes it. `LifetimeStatsUpdater` merges session stats into `maker_lifetime.json` at shutdown. Dashboard gains today/alltime columns and a per-market table — data flows through the existing `MakerDashboardState` dict, no new routes needed.

**Tech Stack:** Python stdlib only (`json`, `os`, `pathlib`, `datetime`). No new dependencies. Tests use `pytest` + `tmp_path` fixture.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `maker/fill_ledger.py` | Append fills to daily JSONL; daily rollover at UTC midnight |
| Create | `maker/state_persistence.py` | `MakerCheckpointer` (atomic write), `MakerStateLoader` (startup resume), `LifetimeStatsUpdater` (merge on shutdown/rollover) |
| Create | `scripts/rebuild_lifetime.py` | CLI: scan all daily fill files → rebuild `maker_lifetime.json` |
| Modify | `maker/state.py` | Add `session_id`, `lifetime_stats`, `daily_fills_seen`, `session_by_market` fields |
| Modify | `maker/inventory.py` | Call `fill_ledger.append()` after `record_fill()` |
| Modify | `maker/runner.py` | Startup load, checkpoint background task, clean-shutdown handler |
| Modify | `maker/dashboard_state.py` | Add today/alltime/by_market fields + `to_json` |
| Modify | `maker/dashboard_loop.py` | Populate new dashboard fields from `MakerState.lifetime_stats` + session delta |
| Modify | `dashboard/static/maker.html` | Three-column P&L bar + collapsible per-market table |
| Create | `tests/maker/test_fill_ledger.py` | FillLedger unit tests |
| Create | `tests/maker/test_state_persistence.py` | Checkpointer / Loader / LifetimeUpdater unit tests |

---

## Task 1: FillLedger

**Files:**
- Create: `maker/fill_ledger.py`
- Create: `tests/maker/test_fill_ledger.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/maker/test_fill_ledger.py
import json
import time
from pathlib import Path
from maker.fill_ledger import FillLedger


def test_append_creates_file(tmp_path):
    ledger = FillLedger(base_dir=tmp_path)
    ledger.append(
        session_id="s1",
        token_id="tok123",
        question="Will X happen?",
        side="BUY",
        price=0.25,
        size=10.0,
        cash_flow=-2.5,
        realized_pnl=0.0,
        inventory_after=10.0,
        filled_at=1_000_000.0,
    )
    files = list((tmp_path / "maker_fills").glob("*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text().strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["token_id"] == "tok123"
    assert record["side"] == "BUY"
    assert record["price"] == 0.25
    assert record["size"] == 10.0
    assert record["cash_flow"] == -2.5
    assert record["realized_pnl"] == 0.0
    assert record["inventory_after"] == 10.0
    assert record["session_id"] == "s1"
    assert "fill_id" in record
    assert "ts" in record


def test_fill_id_is_unique(tmp_path):
    ledger = FillLedger(base_dir=tmp_path)
    ids = set()
    for i in range(5):
        ledger.append(
            session_id="s1", token_id="tok1", question="Q",
            side="BUY", price=0.25, size=10.0,
            cash_flow=-2.5, realized_pnl=0.0, inventory_after=float(i),
            filled_at=1_000_000.0 + i,
        )
    files = list((tmp_path / "maker_fills").glob("*.jsonl"))
    for line in files[0].read_text().strip().splitlines():
        r = json.loads(line)
        ids.add(r["fill_id"])
    assert len(ids) == 5


def test_filename_uses_utc_date(tmp_path):
    ledger = FillLedger(base_dir=tmp_path)
    ts = 1_000_000.0  # 1970-01-12 in UTC
    ledger.append(
        session_id="s1", token_id="tok1", question="Q",
        side="BUY", price=0.5, size=1.0,
        cash_flow=-0.5, realized_pnl=0.0, inventory_after=1.0,
        filled_at=ts,
    )
    files = list((tmp_path / "maker_fills").glob("*.jsonl"))
    assert len(files) == 1
    # filename contains the date corresponding to ts
    from datetime import datetime, timezone
    expected_date = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    assert expected_date in files[0].name


def test_multiple_appends_same_file(tmp_path):
    ledger = FillLedger(base_dir=tmp_path)
    for i in range(3):
        ledger.append(
            session_id="s1", token_id="tok1", question="Q",
            side="BUY", price=0.25, size=10.0,
            cash_flow=-2.5, realized_pnl=0.0, inventory_after=float(i + 1) * 10,
            filled_at=1_000_000.0 + i,
        )
    files = list((tmp_path / "maker_fills").glob("*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text().strip().splitlines()
    assert len(lines) == 3


def test_read_today_returns_records(tmp_path):
    ledger = FillLedger(base_dir=tmp_path)
    ledger.append(
        session_id="s1", token_id="tok1", question="Q",
        side="BUY", price=0.25, size=10.0,
        cash_flow=-2.5, realized_pnl=0.0, inventory_after=10.0,
        filled_at=time.time(),
    )
    records = ledger.read_date()  # today by default
    assert len(records) == 1
    assert records[0]["token_id"] == "tok1"
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/maker/test_fill_ledger.py -v
```
Expected: `ModuleNotFoundError: No module named 'maker.fill_ledger'`

- [ ] **Step 3: Implement FillLedger**

```python
# maker/fill_ledger.py
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
```

- [ ] **Step 4: Run tests — expect pass**

```
pytest tests/maker/test_fill_ledger.py -v
```
Expected: 5 PASSED

- [ ] **Step 5: Commit**

```bash
git add maker/fill_ledger.py tests/maker/test_fill_ledger.py
git commit -m "feat(maker): FillLedger — daily append-only fill JSONL with UTC rollover"
```

---

## Task 2: State Persistence (Checkpointer + Loader + LifetimeUpdater)

**Files:**
- Create: `maker/state_persistence.py`
- Create: `tests/maker/test_state_persistence.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/maker/test_state_persistence.py
import json
import time
from pathlib import Path
from maker.state import MakerState
from maker.state_persistence import MakerCheckpointer, MakerStateLoader, LifetimeStatsUpdater


# ── MakerCheckpointer ────────────────────────────────────────────────────────

def test_checkpointer_writes_json(tmp_path):
    state = MakerState()
    state.session_id = "test-session"
    state.cash_pnl = 5.5
    state.realized_pnl = 2.1
    state.total_fills = 10
    state.total_cancels = 30
    state.inventory["tok1"] = 5.0
    ckpt_path = tmp_path / "ckpt.json"
    MakerCheckpointer.write(state, ckpt_path)
    assert ckpt_path.exists()
    data = json.loads(ckpt_path.read_text())
    assert data["session_id"] == "test-session"
    assert data["cash_pnl"] == 5.5
    assert data["realized_pnl"] == 2.1
    assert data["total_fills"] == 10
    assert data["total_cancels"] == 30
    assert data["inventory"]["tok1"] == 5.0


def test_checkpointer_atomic_no_tmp_left(tmp_path):
    state = MakerState()
    state.session_id = "s"
    ckpt_path = tmp_path / "ckpt.json"
    MakerCheckpointer.write(state, ckpt_path)
    tmp = tmp_path / "ckpt.json.tmp"
    assert not tmp.exists()


# ── MakerStateLoader ─────────────────────────────────────────────────────────

def test_loader_restores_from_checkpoint(tmp_path):
    # Write a checkpoint
    state = MakerState()
    state.session_id = "old-session"
    state.cash_pnl = 12.0
    state.realized_pnl = 3.0
    state.total_fills = 42
    state.total_cancels = 100
    state.inventory["tok1"] = -8.0
    state._open_lots["tok1"] = [["SELL", 0.6, 8.0]]
    state.daily_fills_seen = {"fill-a", "fill-b"}
    ckpt_path = tmp_path / "ckpt.json"
    MakerCheckpointer.write(state, ckpt_path)

    # Load into fresh state
    fresh = MakerState()
    MakerStateLoader.load_checkpoint(fresh, ckpt_path)
    assert fresh.cash_pnl == 12.0
    assert fresh.realized_pnl == 3.0
    assert fresh.total_fills == 42
    assert fresh.total_cancels == 100
    assert fresh.inventory["tok1"] == -8.0
    assert fresh._open_lots["tok1"] == [["SELL", 0.6, 8.0]]
    assert "fill-a" in fresh.daily_fills_seen
    assert "fill-b" in fresh.daily_fills_seen


def test_loader_no_checkpoint_is_noop(tmp_path):
    fresh = MakerState()
    MakerStateLoader.load_checkpoint(fresh, tmp_path / "missing.json")
    assert fresh.cash_pnl == 0.0
    assert fresh.total_fills == 0


def test_loader_replay_adds_unseen_fills(tmp_path):
    from maker.fill_ledger import FillLedger
    ledger = FillLedger(base_dir=tmp_path)
    # Append two fills
    fid1 = ledger.append(
        session_id="s1", token_id="tok1", question="Q", side="BUY",
        price=0.25, size=10.0, cash_flow=-2.5, realized_pnl=0.0,
        inventory_after=10.0, filled_at=time.time(),
    )
    fid2 = ledger.append(
        session_id="s1", token_id="tok1", question="Q", side="SELL",
        price=0.30, size=5.0, cash_flow=1.5, realized_pnl=0.25,
        inventory_after=5.0, filled_at=time.time() + 1,
    )

    fresh = MakerState()
    fresh.session_id = "s2"
    # fid1 already seen (checkpoint captured it), fid2 is new
    fresh.daily_fills_seen = {fid1}
    MakerStateLoader.replay_today(fresh, ledger)
    # Only fid2 replayed: cash_pnl += 1.5, realized_pnl += 0.25
    assert abs(fresh.cash_pnl - 1.5) < 1e-9
    assert abs(fresh.realized_pnl - 0.25) < 1e-9
    assert fresh.total_fills == 1
    assert fid2 in fresh.daily_fills_seen


def test_loader_replay_skips_seen_fills(tmp_path):
    from maker.fill_ledger import FillLedger
    ledger = FillLedger(base_dir=tmp_path)
    fid = ledger.append(
        session_id="s1", token_id="tok1", question="Q", side="BUY",
        price=0.25, size=10.0, cash_flow=-2.5, realized_pnl=0.0,
        inventory_after=10.0, filled_at=time.time(),
    )
    fresh = MakerState()
    fresh.daily_fills_seen = {fid}  # already seen
    MakerStateLoader.replay_today(fresh, ledger)
    assert fresh.cash_pnl == 0.0  # not applied
    assert fresh.total_fills == 0


# ── LifetimeStatsUpdater ─────────────────────────────────────────────────────

def test_lifetime_updater_merge_creates_file(tmp_path):
    state = MakerState()
    state.session_id = "s1"
    state.cash_pnl = 5.0
    state.realized_pnl = 2.0
    state.total_fills = 10
    state.session_by_market = {
        "tok1": {"question": "Q1", "fills": 5, "cash_pnl": 3.0, "realized_pnl": 1.0}
    }
    lt_path = tmp_path / "lifetime.json"
    LifetimeStatsUpdater.merge(state, lt_path)
    assert lt_path.exists()
    data = json.loads(lt_path.read_text())
    assert data["total_fills"] == 10
    assert abs(data["total_cash_pnl"] - 5.0) < 1e-9
    assert abs(data["total_realized_pnl"] - 2.0) < 1e-9
    assert "tok1" in data["by_market"]
    assert data["by_market"]["tok1"]["fills"] == 5


def test_lifetime_updater_merge_accumulates(tmp_path):
    state = MakerState()
    state.session_id = "s1"
    state.cash_pnl = 5.0
    state.realized_pnl = 2.0
    state.total_fills = 10
    state.session_by_market = {
        "tok1": {"question": "Q1", "fills": 5, "cash_pnl": 3.0, "realized_pnl": 1.0}
    }
    lt_path = tmp_path / "lifetime.json"
    # Write initial
    LifetimeStatsUpdater.merge(state, lt_path)
    # Second session
    state2 = MakerState()
    state2.session_id = "s2"
    state2.cash_pnl = 3.0
    state2.realized_pnl = 1.0
    state2.total_fills = 6
    state2.session_by_market = {
        "tok1": {"question": "Q1", "fills": 3, "cash_pnl": 1.5, "realized_pnl": 0.5}
    }
    LifetimeStatsUpdater.merge(state2, lt_path)
    data = json.loads(lt_path.read_text())
    assert data["total_fills"] == 16
    assert abs(data["total_cash_pnl"] - 8.0) < 1e-9
    assert data["by_market"]["tok1"]["fills"] == 8
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/maker/test_state_persistence.py -v
```
Expected: `ModuleNotFoundError: No module named 'maker.state_persistence'`

- [ ] **Step 3: Add new fields to MakerState first** (tests reference `session_id`, `daily_fills_seen`, `session_by_market`)

Edit `maker/state.py` — add after `last_fill_times` field (line 55):

```python
    # Persistence fields — populated by MakerStateLoader on startup
    session_id: str = ""
    daily_fills_seen: set = field(default_factory=set)

    # Per-market fill/P&L accumulator for current session
    # { token_id: { "question": str, "fills": int, "cash_pnl": float, "realized_pnl": float } }
    session_by_market: dict = field(default_factory=dict)

    # Lifetime stats loaded from maker_lifetime.json on startup
    # { "total_fills": int, "total_cash_pnl": float, "total_realized_pnl": float,
    #   "by_market": { token_id: {...} }, "by_date": { date_str: {...} } }
    lifetime_stats: dict = field(default_factory=dict)
```

Also update `record_fill()` in `maker/state.py` to accumulate `session_by_market`. The FIFO block already updates `self.realized_pnl` — capture the delta before and after. The full addition goes **at the start of `record_fill`** (capture `_realized_before`) and **at the end** (after `if len(self.fill_history) > 100`):

At the very start of `record_fill`, before any other code:
```python
        _realized_before = self.realized_pnl
```

After `if len(self.fill_history) > 100: self.fill_history.pop()`, add:
```python
        # Per-market session accumulator (for lifetime stats merge at shutdown)
        if token_id not in self.session_by_market:
            self.session_by_market[token_id] = {
                "question": question, "fills": 0, "cash_pnl": 0.0, "realized_pnl": 0.0
            }
        mkt = self.session_by_market[token_id]
        mkt["fills"] += 1
        if side == "SELL":
            mkt["cash_pnl"] += price * size
        else:
            mkt["cash_pnl"] -= price * size
        mkt["realized_pnl"] += self.realized_pnl - _realized_before
```

This captures the per-fill FIFO realized_pnl delta correctly per market.

- [ ] **Step 4: Implement state_persistence.py**

```python
# maker/state_persistence.py
"""Checkpoint writer, startup loader, and lifetime stats updater for the maker bot."""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from maker.state import MakerState
    from maker.fill_ledger import FillLedger


class MakerCheckpointer:
    """Writes a full MakerState snapshot atomically to disk."""

    @staticmethod
    def write(state: "MakerState", path: str | Path) -> None:
        """Serialize MakerState to JSON. Atomic: writes .tmp then renames."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        snapshot = {
            "session_id": state.session_id,
            "checkpoint_at": time.time(),
            "inventory": dict(state.inventory),
            "cash_pnl": state.cash_pnl,
            "realized_pnl": state.realized_pnl,
            "total_fills": state.total_fills,
            "total_cancels": state.total_cancels,
            # uptime_ticks / total_ticks not stored in MakerState — dashboard_loop owns them
            "open_lots": {
                tid: [[s, p, sz] for s, p, sz in lots]
                for tid, lots in state._open_lots.items()
            },
            "daily_fills_seen": list(state.daily_fills_seen),
            "session_by_market": dict(state.session_by_market),
        }

        tmp = str(path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2)
        os.replace(tmp, path)


class MakerStateLoader:
    """Restores MakerState from checkpoint + daily fill replay on startup."""

    @staticmethod
    def load_checkpoint(state: "MakerState", path: str | Path) -> None:
        """Load checkpoint into state. No-op if file does not exist."""
        path = Path(path)
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return

        state.session_id = data.get("session_id", "")
        state.cash_pnl = float(data.get("cash_pnl", 0.0))
        state.realized_pnl = float(data.get("realized_pnl", 0.0))
        state.total_fills = int(data.get("total_fills", 0))
        state.total_cancels = int(data.get("total_cancels", 0))
        state.inventory = {k: float(v) for k, v in data.get("inventory", {}).items()}
        state._open_lots = {
            tid: [[row[0], float(row[1]), float(row[2])] for row in lots]
            for tid, lots in data.get("open_lots", {}).items()
        }
        state.daily_fills_seen = set(data.get("daily_fills_seen", []))
        state.session_by_market = data.get("session_by_market", {})

    @staticmethod
    def replay_today(state: "MakerState", ledger: "FillLedger") -> None:
        """Replay unseen fills from today's fill file to recover <60s gap after crash."""
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        records = ledger.read_date(today)
        for rec in records:
            fid = rec.get("fill_id", "")
            if fid in state.daily_fills_seen:
                continue
            # Re-apply cash_pnl and realized_pnl from the stored values
            state.cash_pnl += float(rec.get("cash_flow", 0.0))
            state.realized_pnl += float(rec.get("realized_pnl", 0.0))
            state.total_fills += 1
            state.daily_fills_seen.add(fid)
            # Restore inventory
            token_id = rec.get("token_id", "")
            if token_id:
                state.inventory[token_id] = float(rec.get("inventory_after", 0.0))

    @staticmethod
    def load_lifetime(state: "MakerState", path: str | Path) -> None:
        """Load maker_lifetime.json into state.lifetime_stats. No-op if missing."""
        path = Path(path)
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            state.lifetime_stats = data
        except (json.JSONDecodeError, OSError):
            state.lifetime_stats = {}

    @staticmethod
    def load(
        state: "MakerState",
        ledger: "FillLedger",
        checkpoint_path: str | Path = "maker_data/maker_checkpoint.json",
        lifetime_path: str | Path = "maker_data/maker_lifetime.json",
    ) -> None:
        """Full startup sequence: checkpoint → fill replay → lifetime stats."""
        MakerStateLoader.load_checkpoint(state, checkpoint_path)
        MakerStateLoader.replay_today(state, ledger)
        MakerStateLoader.load_lifetime(state, lifetime_path)


class LifetimeStatsUpdater:
    """Merges current session stats into maker_lifetime.json at shutdown / daily rollover."""

    @staticmethod
    def merge(
        state: "MakerState",
        path: str | Path = "maker_data/maker_lifetime.json",
        date_str: str | None = None,
    ) -> None:
        """Merge session stats from MakerState into the lifetime JSON file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Load existing lifetime data
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = {}
        else:
            data = {}

        # Top-level totals
        data["last_updated"] = time.time()
        data["total_fills"] = data.get("total_fills", 0) + state.total_fills
        data["total_cash_pnl"] = data.get("total_cash_pnl", 0.0) + state.cash_pnl
        data["total_realized_pnl"] = data.get("total_realized_pnl", 0.0) + state.realized_pnl

        # Per-market breakdown
        by_market = data.setdefault("by_market", {})
        for token_id, mkt in state.session_by_market.items():
            entry = by_market.setdefault(token_id, {
                "question": mkt.get("question", ""),
                "fills": 0,
                "cash_pnl": 0.0,
                "realized_pnl": 0.0,
            })
            entry["question"] = mkt.get("question", entry["question"])
            entry["fills"] += mkt.get("fills", 0)
            entry["cash_pnl"] += mkt.get("cash_pnl", 0.0)
            entry["realized_pnl"] += mkt.get("realized_pnl", 0.0)

        # By-date entry
        if date_str is None:
            date_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        by_date = data.setdefault("by_date", {})
        day = by_date.setdefault(date_str, {"fills": 0, "cash_pnl": 0.0, "realized_pnl": 0.0})
        day["fills"] += state.total_fills
        day["cash_pnl"] += state.cash_pnl
        day["realized_pnl"] += state.realized_pnl

        # Atomic write
        tmp = str(path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
```

- [ ] **Step 5: Run tests — expect pass**

```
pytest tests/maker/test_state_persistence.py -v
```
Expected: all tests PASS

- [ ] **Step 6: Run existing state tests to verify no regression**

```
pytest tests/maker/test_state.py -v
```
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add maker/state.py maker/state_persistence.py tests/maker/test_state_persistence.py
git commit -m "feat(maker): state persistence — checkpointer, loader, lifetime updater"
```

---

## Task 3: Wire FillLedger into InventoryManager

**Files:**
- Modify: `maker/inventory.py`

- [ ] **Step 1: Update InventoryManager to accept and call FillLedger**

Edit `maker/inventory.py`:

Replace the `__init__` signature:
```python
    def __init__(
        self,
        maker_state: MakerState,
        fills_q: asyncio.Queue,
        skew_updates_q: asyncio.Queue,
        cancel_q: asyncio.Queue,
        bankroll: float = 500.0,
        app_state: AppState | None = None,
        markout_q: asyncio.Queue | None = None,
        fill_ledger=None,
    ):
        self._maker = maker_state
        self._app = app_state
        self._fills_q = fills_q
        self._skew_q = skew_updates_q
        self._cancel_q = cancel_q
        self._markout_q = markout_q
        self._fill_ledger = fill_ledger
        self._max_daily_loss = bankroll * MAX_DAILY_LOSS_PCT
```

In `handle_fill()`, add after `self._maker.record_fill(...)` call (after line 55):

```python
        # Persist fill to daily ledger (non-blocking synchronous write)
        if self._fill_ledger is not None:
            fid = self._fill_ledger.append(
                session_id=self._maker.session_id,
                token_id=fill.token_id,
                question=question,
                side=fill.side,
                price=fill.price,
                size=fill.size,
                cash_flow=(-fill.price * fill.size if fill.side == "BUY"
                           else fill.price * fill.size),
                realized_pnl=self._maker.realized_pnl,  # current cumulative total
                inventory_after=self._maker.get_inventory(fill.token_id),
                filled_at=fill.filled_at,
            )
            self._maker.daily_fills_seen.add(fid)
```

- [ ] **Step 2: Verify existing inventory tests still pass**

```
pytest tests/maker/test_inventory.py -v
```
Expected: all PASS (fill_ledger=None by default, so no change to existing paths)

- [ ] **Step 3: Commit**

```bash
git add maker/inventory.py
git commit -m "feat(maker): wire FillLedger into InventoryManager.handle_fill"
```

---

## Task 4: Wire Persistence into Runner

**Files:**
- Modify: `maker/runner.py`

- [ ] **Step 1: Update `build_maker_actors` to accept and pass fill_ledger**

Edit `maker/runner.py`. Replace `build_maker_actors` function:

```python
def build_maker_actors(
    app_state: AppState,
    paper: bool = True,
    shadow: bool = False,
    clob=None,
    bankroll: float = 500.0,
    fill_ledger=None,
) -> tuple[dict, dict]:
    """Create all actors and queues. Returns (actors_dict, queues_dict)."""
    maker_state = MakerState()

    # Queues
    active_markets_q = asyncio.Queue()
    quote_intents_q = asyncio.Queue()
    fills_q = asyncio.Queue()
    skew_updates_q = asyncio.Queue()
    cancel_q = asyncio.Queue()
    price_update_q = asyncio.Queue(maxsize=200)
    markout_q: asyncio.Queue = asyncio.Queue()
    trades_q: asyncio.Queue | None = asyncio.Queue(maxsize=500) if shadow else None

    queues = {
        "active_markets_q": active_markets_q,
        "quote_intents_q": quote_intents_q,
        "fills_q": fills_q,
        "skew_updates_q": skew_updates_q,
        "cancel_q": cancel_q,
        "price_update_q": price_update_q,
        "markout_q": markout_q,
        "trades_q": trades_q,
    }

    if shadow:
        fill_poller = ShadowFillPoller(app_state, maker_state, fills_q, trades_q)
    else:
        fill_poller = FillPoller(app_state, maker_state, fills_q, clob=clob, paper=paper)

    actors = {
        "selector": MarketSelector(app_state, active_markets_q),
        "quote_engine": QuoteEngine(
            app_state, maker_state, active_markets_q, quote_intents_q, skew_updates_q,
            price_update_q=price_update_q,
        ),
        "order_manager": OrderManager(
            maker_state, clob=clob, paper=True,
            quote_intents_q=quote_intents_q, cancel_q=cancel_q,
        ),
        "fill_poller": fill_poller,
        "inventory": InventoryManager(
            maker_state, fills_q, skew_updates_q, cancel_q,
            bankroll=bankroll, app_state=app_state, markout_q=markout_q,
            fill_ledger=fill_ledger,
        ),
        "markout_tracker": MarkoutTracker(app_state, markout_q),
    }

    return actors, queues
```

- [ ] **Step 2: Update `run_maker()` to add startup load, checkpoint loop, and shutdown**

Add imports at the top of the imports block in `run_maker()`:

```python
    from maker.fill_ledger import FillLedger
    from maker.state_persistence import MakerCheckpointer, MakerStateLoader, LifetimeStatsUpdater
    from datetime import datetime, timezone
```

After `app_state = AppState()` add:

```python
    # ── State persistence setup ───────────────────────────────────────────────
    _DATA_DIR = "maker_data"
    _CKPT_PATH = f"{_DATA_DIR}/maker_checkpoint.json"
    _LIFETIME_PATH = f"{_DATA_DIR}/maker_lifetime.json"
    fill_ledger = FillLedger(base_dir=_DATA_DIR)
```

After `actors, queues = build_maker_actors(...)` change the call to pass `fill_ledger`:

```python
    actors, queues = build_maker_actors(
        app_state=app_state,
        paper=paper,
        shadow=shadow,
        clob=clob,
        bankroll=config.BANKROLL_USDC,
        fill_ledger=fill_ledger,
    )
```

After `maker_state_ref = actors["order_manager"]._maker` add the startup load and session_id:

```python
    # ── Load persisted state ──────────────────────────────────────────────────
    MakerStateLoader.load(
        maker_state_ref, fill_ledger,
        checkpoint_path=_CKPT_PATH,
        lifetime_path=_LIFETIME_PATH,
    )
    maker_state_ref.session_id = datetime.now(timezone.utc).isoformat(timespec="seconds")
    log.info(
        f"State loaded — fills={maker_state_ref.total_fills} "
        f"cash_pnl={maker_state_ref.cash_pnl:.2f} "
        f"realized_pnl={maker_state_ref.realized_pnl:.2f} "
        f"inventory_markets={len(maker_state_ref.inventory)}"
    )
```

Add a checkpoint background coroutine (add before `coros = [...]`):

```python
    async def _checkpoint_loop():
        while True:
            await asyncio.sleep(60)
            MakerCheckpointer.write(maker_state_ref, _CKPT_PATH)
            log.debug("Checkpoint written")
```

Add `_checkpoint_loop()` to `coros`:

```python
    coros.append(_checkpoint_loop())
```

Wrap `await asyncio.gather(*coros)` in try/finally for clean shutdown:

```python
    try:
        await asyncio.gather(*coros)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        # Clean shutdown: write final checkpoint + merge lifetime stats
        MakerCheckpointer.write(maker_state_ref, _CKPT_PATH)
        LifetimeStatsUpdater.merge(maker_state_ref, _LIFETIME_PATH)
        fill_ledger.close()
        log.info(
            f"Maker state saved cleanly — fills={maker_state_ref.total_fills} "
            f"realized_pnl={maker_state_ref.realized_pnl:.2f}"
        )
```

- [ ] **Step 3: Verify runner tests still pass**

```
pytest tests/maker/test_runner.py -v
```
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add maker/runner.py
git commit -m "feat(maker): wire persistence into runner — startup load, checkpoint loop, clean shutdown"
```

---

## Task 5: Dashboard State & Loop — New Fields

**Files:**
- Modify: `maker/dashboard_state.py`
- Modify: `maker/dashboard_loop.py`

- [ ] **Step 1: Add new fields to MakerDashboardState**

Edit `maker/dashboard_state.py`. Add new fields in `__init__` after `self.falcon_data: dict = {}`:

```python
        # ── Lifetime / today / session split ──────────────────────────────
        # Today = by_date[today] from lifetime stats + current session delta
        self.today_fills: int = 0
        self.today_realized_pnl: float = 0.0
        self.today_cash_pnl: float = 0.0
        # All time = lifetime totals + current session delta
        self.alltime_fills: int = 0
        self.alltime_realized_pnl: float = 0.0
        self.alltime_cash_pnl: float = 0.0
        self.alltime_markets: int = 0
        # Per-market lifetime breakdown: list of dicts sorted by realized_pnl desc
        self.by_market: list = []
```

Add new fields to `to_json()` — replace the `return json.dumps({...})` block's closing with:

```python
                "today_fills": self.today_fills,
                "today_realized_pnl": self.today_realized_pnl,
                "today_cash_pnl": self.today_cash_pnl,
                "alltime_fills": self.alltime_fills,
                "alltime_realized_pnl": self.alltime_realized_pnl,
                "alltime_cash_pnl": self.alltime_cash_pnl,
                "alltime_markets": self.alltime_markets,
                "by_market": self.by_market,
```

(Add these lines before the closing `}` of the `json.dumps` dict.)

- [ ] **Step 2: Populate new fields in dashboard_loop**

Edit `maker/dashboard_loop.py`. In `maker_dashboard_loop()`, add after the `async with maker_state._lock:` block that reads `realized_pnl`:

```python
                session_id = maker_state.session_id
                lifetime_stats = dict(maker_state.lifetime_stats)
                session_by_market = dict(maker_state.session_by_market)
                total_fills_session = maker_state.total_fills
                cash_pnl_session = maker_state.cash_pnl
                realized_pnl_session = maker_state.realized_pnl
```

Then add a helper block before `dash.update({...})` to compute today/alltime:

```python
            # ── Lifetime / today split ────────────────────────────────────
            from datetime import datetime as _dt
            today_str = _dt.utcnow().strftime("%Y-%m-%d")
            lt_today = lifetime_stats.get("by_date", {}).get(today_str, {})
            today_fills = lt_today.get("fills", 0) + total_fills_session
            today_cash_pnl = lt_today.get("cash_pnl", 0.0) + cash_pnl_session
            today_realized_pnl = lt_today.get("realized_pnl", 0.0) + realized_pnl_session

            lt_totals_fills = lifetime_stats.get("total_fills", 0)
            lt_totals_cash = lifetime_stats.get("total_cash_pnl", 0.0)
            lt_totals_realized = lifetime_stats.get("total_realized_pnl", 0.0)
            alltime_fills = lt_totals_fills + total_fills_session
            alltime_cash_pnl = lt_totals_cash + cash_pnl_session
            alltime_realized_pnl = lt_totals_realized + realized_pnl_session

            # Per-market: merge lifetime data with current session data
            lt_by_market = lifetime_stats.get("by_market", {})
            merged_markets: dict[str, dict] = {}
            for tid, entry in lt_by_market.items():
                merged_markets[tid] = dict(entry)
            for tid, sess in session_by_market.items():
                if tid not in merged_markets:
                    merged_markets[tid] = {
                        "question": sess.get("question", ""),
                        "fills": 0, "cash_pnl": 0.0, "realized_pnl": 0.0,
                    }
                m = merged_markets[tid]
                m["fills"] += sess.get("fills", 0)
                m["cash_pnl"] += sess.get("cash_pnl", 0.0)
                m["realized_pnl"] += sess.get("realized_pnl", 0.0)
            by_market_list = sorted(
                [{"token_id": tid, **v} for tid, v in merged_markets.items()],
                key=lambda x: -x.get("realized_pnl", 0.0),
            )
            alltime_markets = len(merged_markets)
```

Add new keys to the `dash.update({...})` call:

```python
                "today_fills": today_fills,
                "today_realized_pnl": round(today_realized_pnl, 4),
                "today_cash_pnl": round(today_cash_pnl, 4),
                "alltime_fills": alltime_fills,
                "alltime_realized_pnl": round(alltime_realized_pnl, 4),
                "alltime_cash_pnl": round(alltime_cash_pnl, 4),
                "alltime_markets": alltime_markets,
                "by_market": by_market_list[:50],
```

- [ ] **Step 3: Run integration test**

```
pytest tests/maker/test_integration.py -v
```
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add maker/dashboard_state.py maker/dashboard_loop.py
git commit -m "feat(maker): dashboard today/alltime/per-market fields"
```

---

## Task 6: Dashboard HTML — Three-Column P&L + Per-Market Table

**Files:**
- Modify: `dashboard/static/maker.html`

- [ ] **Step 1: Replace topbar P&L stats with three-column layout**

In `maker.html`, find the block (lines 329–342):

```html
  <div class="stat">
    <span class="stat-label">Portfolio P&amp;L</span>
    <span class="stat-value" id="daily-pnl" title="Cash flows + open positions marked at current mid price">—</span>
  </div>

  <div class="stat">
    <span class="stat-label">Net Cash Flows</span>
    <span class="stat-value" id="cash-pnl" title="Total sell proceeds minus total buy costs — not booked profit while positions remain open">—</span>
  </div>

  <div class="stat">
    <span class="stat-label">Realized P&amp;L</span>
    <span class="stat-value" id="realized-pnl" title="Booked profit from completed round trips only (FIFO matched). This is locked in.">—</span>
  </div>
```

Replace with:

```html
  <!-- Session column -->
  <div class="stat-group">
    <div class="stat-group-label">SESSION</div>
    <div class="stat">
      <span class="stat-label">MTM P&amp;L</span>
      <span class="stat-value" id="daily-pnl" title="Cash flows + open positions marked at current mid">—</span>
    </div>
    <div class="stat">
      <span class="stat-label">Cash Flows</span>
      <span class="stat-value" id="cash-pnl">—</span>
    </div>
    <div class="stat">
      <span class="stat-label">Realized</span>
      <span class="stat-value" id="realized-pnl">—</span>
    </div>
  </div>

  <!-- Today column -->
  <div class="stat-group">
    <div class="stat-group-label">TODAY</div>
    <div class="stat">
      <span class="stat-label">Fills</span>
      <span class="stat-value" id="today-fills">—</span>
    </div>
    <div class="stat">
      <span class="stat-label">Cash P&amp;L</span>
      <span class="stat-value" id="today-cash-pnl">—</span>
    </div>
    <div class="stat">
      <span class="stat-label">Realized</span>
      <span class="stat-value" id="today-realized-pnl">—</span>
    </div>
  </div>

  <!-- All Time column -->
  <div class="stat-group">
    <div class="stat-group-label">ALL TIME</div>
    <div class="stat">
      <span class="stat-label">Fills</span>
      <span class="stat-value" id="alltime-fills">—</span>
    </div>
    <div class="stat">
      <span class="stat-label">Cash P&amp;L</span>
      <span class="stat-value" id="alltime-cash-pnl">—</span>
    </div>
    <div class="stat">
      <span class="stat-label">Realized</span>
      <span class="stat-value" id="alltime-realized-pnl">—</span>
    </div>
  </div>
```

- [ ] **Step 2: Add CSS for stat-group**

In the `<style>` block (around line 50 after `.stat` rules), add:

```css
    .stat-group {
      display: flex;
      flex-direction: column;
      gap: 4px;
      padding: 4px 10px;
      border-left: 1px solid #30363d;
    }
    .stat-group-label {
      font-size: 9px;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      color: #58a6ff;
      margin-bottom: 2px;
    }
```

- [ ] **Step 3: Add per-market table panel**

In the HTML, after the Fill History panel (find `<!-- Fill History -->` section end `</div>` around line 430), add:

```html
  <!-- Per-Market Performance -->
  <div class="panel panel-full" id="by-market-panel">
    <div class="panel-header" style="cursor:pointer" onclick="toggleByMarket()">
      Market Performance (Lifetime)
      <span class="count" id="by-market-count">0</span>
      <span style="margin-left:auto;font-size:11px;color:#8b949e" id="by-market-toggle">▼</span>
    </div>
    <div id="by-market-body" class="scroll-body">
      <table id="by-market-table">
        <thead>
          <tr>
            <th>Market</th>
            <th>Fills</th>
            <th>Realized P&amp;L</th>
            <th>Cash P&amp;L</th>
          </tr>
        </thead>
        <tbody id="by-market-rows">
          <tr class="empty-row"><td colspan="4">No data yet</td></tr>
        </tbody>
      </table>
    </div>
  </div>
```

- [ ] **Step 4: Add JavaScript render logic**

In the `render(data)` function, add after the existing `document.getElementById('cb-fires')` line:

```javascript
  // Today stats
  const todayFillsEl = document.getElementById('today-fills');
  if (todayFillsEl) todayFillsEl.textContent = (data.today_fills || 0).toLocaleString();
  const todayCashEl = document.getElementById('today-cash-pnl');
  if (todayCashEl) { todayCashEl.textContent = fmtPnl(data.today_cash_pnl || 0); todayCashEl.className = 'stat-value ' + pnlClass(data.today_cash_pnl || 0); }
  const todayRealEl = document.getElementById('today-realized-pnl');
  if (todayRealEl) { todayRealEl.textContent = fmtPnl(data.today_realized_pnl || 0); todayRealEl.className = 'stat-value ' + pnlClass(data.today_realized_pnl || 0); }

  // All-time stats
  const atFillsEl = document.getElementById('alltime-fills');
  if (atFillsEl) atFillsEl.textContent = (data.alltime_fills || 0).toLocaleString();
  const atCashEl = document.getElementById('alltime-cash-pnl');
  if (atCashEl) { atCashEl.textContent = fmtPnl(data.alltime_cash_pnl || 0); atCashEl.className = 'stat-value ' + pnlClass(data.alltime_cash_pnl || 0); }
  const atRealEl = document.getElementById('alltime-realized-pnl');
  if (atRealEl) { atRealEl.textContent = fmtPnl(data.alltime_realized_pnl || 0); atRealEl.className = 'stat-value ' + pnlClass(data.alltime_realized_pnl || 0); }

  // Per-market table
  const byMarket = data.by_market || [];
  const bmCount = document.getElementById('by-market-count');
  if (bmCount) bmCount.textContent = byMarket.length;
  const bmRows = document.getElementById('by-market-rows');
  if (bmRows) {
    if (byMarket.length === 0) {
      bmRows.innerHTML = '<tr class="empty-row"><td colspan="4">No data yet</td></tr>';
    } else {
      bmRows.innerHTML = byMarket.map(m => {
        const rp = m.realized_pnl || 0;
        const cp = m.cash_pnl || 0;
        return `<tr>
          <td title="${m.token_id}">${escHtml((m.question || m.token_id || '').slice(0, 60))}</td>
          <td>${(m.fills || 0).toLocaleString()}</td>
          <td class="${pnlClass(rp)}">${fmtPnl(rp)}</td>
          <td class="${pnlClass(cp)}">${fmtPnl(cp)}</td>
        </tr>`;
      }).join('');
    }
  }
```

Add toggle function somewhere in the `<script>` block:

```javascript
let _byMarketOpen = true;
function toggleByMarket() {
  _byMarketOpen = !_byMarketOpen;
  document.getElementById('by-market-body').style.display = _byMarketOpen ? '' : 'none';
  document.getElementById('by-market-toggle').textContent = _byMarketOpen ? '▼' : '▶';
}
```

- [ ] **Step 5: Start the bot briefly and verify the dashboard renders at http://127.0.0.1:5050/maker**

Check visually that three columns appear and the per-market table shows "No data yet" until fills arrive.

- [ ] **Step 6: Commit**

```bash
git add dashboard/static/maker.html
git commit -m "feat(maker): dashboard three-column P&L + per-market lifetime table"
```

---

## Task 7: Rebuild Script

**Files:**
- Create: `scripts/rebuild_lifetime.py`

- [ ] **Step 1: Create the rebuild script**

```python
#!/usr/bin/env python3
# scripts/rebuild_lifetime.py
"""Rebuild maker_lifetime.json from all daily fill files.

Usage:
    python scripts/rebuild_lifetime.py
    python scripts/rebuild_lifetime.py --data-dir /path/to/maker_data

Run this if maker_lifetime.json is missing or corrupted.
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def rebuild(data_dir: str = "maker_data") -> None:
    fills_dir = Path(data_dir) / "maker_fills"
    lt_path = Path(data_dir) / "maker_lifetime.json"

    if not fills_dir.exists():
        print(f"No fills directory found at {fills_dir}")
        return

    data: dict = {
        "last_updated": time.time(),
        "total_fills": 0,
        "total_cash_pnl": 0.0,
        "total_realized_pnl": 0.0,
        "by_market": {},
        "by_date": {},
    }

    fill_files = sorted(fills_dir.glob("maker_fills_*.jsonl"))
    if not fill_files:
        print("No fill files found. Nothing to rebuild.")
        return

    total_lines = 0
    errors = 0

    for fill_file in fill_files:
        # Extract date from filename: maker_fills_YYYY-MM-DD.jsonl
        parts = fill_file.stem.split("_")
        date_str = parts[-1] if len(parts) >= 3 else "unknown"
        day_entry = data["by_date"].setdefault(date_str, {
            "fills": 0, "cash_pnl": 0.0, "realized_pnl": 0.0
        })

        with open(fill_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    errors += 1
                    continue

                total_lines += 1
                cash_flow = float(rec.get("cash_flow", 0.0))
                realized_pnl = float(rec.get("realized_pnl", 0.0))
                token_id = rec.get("token_id", "")
                question = rec.get("question", "")

                data["total_fills"] += 1
                data["total_cash_pnl"] += cash_flow
                day_entry["fills"] += 1
                day_entry["cash_pnl"] += cash_flow

                if token_id:
                    entry = data["by_market"].setdefault(token_id, {
                        "question": question,
                        "fills": 0,
                        "cash_pnl": 0.0,
                        "realized_pnl": 0.0,
                    })
                    entry["question"] = question or entry["question"]
                    entry["fills"] += 1
                    entry["cash_pnl"] += cash_flow

    # realized_pnl: use last record's cumulative value per market (stored as running total)
    # Re-scan to get last realized_pnl per market
    last_realized: dict[str, float] = {}
    for fill_file in fill_files:
        with open(fill_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    tid = rec.get("token_id", "")
                    rp = rec.get("realized_pnl", 0.0)
                    if tid:
                        last_realized[tid] = float(rp)
                except json.JSONDecodeError:
                    pass

    # Note: realized_pnl in fill records is the cumulative total at time of fill,
    # not a per-fill delta. Use total_realized_pnl from latest checkpoint instead.
    # For rebuild, sum per-market last_realized values as best approximation.
    total_realized = sum(last_realized.values())
    data["total_realized_pnl"] = total_realized
    for tid, rp in last_realized.items():
        if tid in data["by_market"]:
            data["by_market"][tid]["realized_pnl"] = rp

    tmp = str(lt_path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, lt_path)

    print(f"Rebuilt {lt_path}")
    print(f"  Files scanned: {len(fill_files)}")
    print(f"  Total lines:   {total_lines}")
    print(f"  Errors:        {errors}")
    print(f"  Total fills:   {data['total_fills']}")
    print(f"  Markets:       {len(data['by_market'])}")
    print(f"  Date range:    {min(data['by_date'].keys())} → {max(data['by_date'].keys())}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="maker_data")
    args = parser.parse_args()
    rebuild(args.data_dir)
```

- [ ] **Step 2: Test the script runs without error on empty data**

```
python scripts/rebuild_lifetime.py
```
Expected output: `No fills directory found at maker_data/maker_fills` (or similar — no crash)

- [ ] **Step 3: Commit**

```bash
git add scripts/rebuild_lifetime.py
git commit -m "feat(maker): rebuild_lifetime.py — regenerate lifetime stats from daily fill files"
```

---

## Task 8: Full Integration Smoke Test

**Files:**
- Review: existing `tests/maker/test_integration.py`

- [ ] **Step 1: Run full test suite**

```
pytest tests/maker/ -v
```
Expected: all existing tests PASS, new tests PASS. Note any failures and fix before proceeding.

- [ ] **Step 2: Run the bot in paper mode for 60 seconds and verify persistence**

```bash
python main.py --mode maker
# Wait 60s for first checkpoint write
# Ctrl+C to stop
```

Check that these files exist:
```bash
ls maker_data/
# Expected: maker_checkpoint.json  maker_lifetime.json  maker_fills/
ls maker_data/maker_fills/
# Expected: maker_fills_YYYY-MM-DD.jsonl
```

- [ ] **Step 3: Restart and verify state is restored**

```bash
python main.py --mode maker
# Check logs for: "State loaded — fills=N cash_pnl=X realized_pnl=Y inventory_markets=Z"
# N should match total from previous session
```

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat(maker): complete state persistence — checkpoint, fill ledger, lifetime stats, dashboard"
```
