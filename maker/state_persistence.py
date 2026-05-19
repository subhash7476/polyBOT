"""Persistence layer for MakerState — checkpoints, lifetime cache, startup loader."""

import asyncio
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from utils.logger import get_logger

if TYPE_CHECKING:
    from maker.state import MakerState
    from maker.fill_ledger import FillLedger

log = get_logger(__name__)

_BASE = Path("maker_data")


class MakerCheckpointer:
    """Saves MakerState to disk every `interval` seconds and on shutdown."""

    def __init__(
        self,
        maker_state: "MakerState",
        base_dir: Path = _BASE,
        fill_ledger: "FillLedger | None" = None,
        lifetime_cache: "LifetimeStatsCache | None" = None,
    ):
        self._state = maker_state
        self._path = Path(base_dir) / "maker_checkpoint.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fill_ledger = fill_ledger
        self._lifetime_cache = lifetime_cache
        self._last_date = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    def save(self) -> None:
        """Atomically write current inventory/P&L state to checkpoint file."""
        s = self._state
        data = {
            "saved_at": time.time(),
            "session_id": s.session_id,
            "inventory": s.inventory,
            "inventory_entry_time": s.inventory_entry_time,
            "cash_pnl": s.cash_pnl,
            "realized_pnl": s.realized_pnl,
            "open_lots": {k: [list(lot) for lot in v] for k, v in s._open_lots.items()},
            "total_fills": s.total_fills,
            "total_cancels": s.total_cancels,
            "cooldowns": s.cooldowns,
            "inventory_cap_hits": s.inventory_cap_hits,
            "daily_fills_seen": list(s.daily_fills_seen),
        }
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)
        log.debug(f"Checkpoint saved: fills={s.total_fills} cash={s.cash_pnl:.4f}")

    async def checkpoint_loop(self, interval: float = 60.0) -> None:
        """Periodic checkpoint coroutine — add to asyncio.gather in runner."""
        while True:
            await asyncio.sleep(interval)
            try:
                async with self._state._lock:
                    self.save()
            except Exception as exc:
                log.warning(f"Checkpoint save failed: {exc}")

            # Detect UTC midnight crossing and update the lifetime cache for the completed day.
            today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
            if today != self._last_date and self._fill_ledger and self._lifetime_cache:
                self._last_date = today
                try:
                    stats = self._lifetime_cache.update_through_yesterday(self._fill_ledger)
                    async with self._state._lock:
                        self._state.lifetime_stats = stats
                        # Fold yesterday's session fills into pre-midnight accumulators
                        # so Session P&L keeps spanning the full runtime, then clear
                        # session_by_market so "Today" starts clean for the new UTC day.
                        sbm = self._state.session_by_market
                        self._state.session_pre_midnight_fills += sum(
                            m.get("fills", 0) for m in sbm.values()
                        )
                        self._state.session_pre_midnight_cash += sum(
                            m.get("cash_pnl", 0.0) for m in sbm.values()
                        )
                        self._state.session_pre_midnight_realized += sum(
                            m.get("realized_pnl", 0.0) for m in sbm.values()
                        )
                        self._state.session_by_market.clear()
                        self._state.today_stats = {"fills": 0, "cash_pnl": 0.0,
                                                   "realized_pnl": 0.0, "by_market": {}}
                    log.info(
                        f"Midnight rollover: lifetime cache updated through "
                        f"{stats.get('last_date', '?')} "
                        f"({stats.get('total_fills', 0)} total fills); "
                        f"session_by_market reset for new UTC day"
                    )
                except Exception as exc:
                    log.warning(f"Midnight rollover lifetime update failed: {exc}")


class LifetimeStatsCache:
    """Maintains maker_data/maker_lifetime.json covering all fill dates BEFORE today.

    Design: the cache is intentionally kept to dates < today. Today's fills live in
    the fill ledger file and are read fresh each startup as today_stats. This avoids
    double-counting across restarts on the same calendar day.
    """

    _ZERO_DATE = {"fills": 0, "realized_pnl": 0.0, "cash_pnl": 0.0}
    _ZERO_MARKET = {"fills": 0, "realized_pnl": 0.0, "cash_pnl": 0.0, "question": ""}

    def __init__(self, base_dir: Path = _BASE):
        self._path = Path(base_dir) / "maker_lifetime.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                # Normalize: older files may be missing keys or have null last_date
                data.setdefault("by_market", {})
                data.setdefault("by_date", {})
                data.setdefault("total_fills", 0)
                data.setdefault("total_cash_pnl", 0.0)
                data.setdefault("total_realized_pnl", 0.0)
                data["last_date"] = data.get("last_date") or ""
                return data
            except Exception:
                pass
        return {"last_date": "", "total_fills": 0, "total_realized_pnl": 0.0,
                "total_cash_pnl": 0.0, "by_date": {}, "by_market": {}}

    def _save(self, stats: dict) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(stats, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)

    def update_through_yesterday(self, fill_ledger: "FillLedger") -> dict:
        """Ensure the cache covers all fill dates up to and including yesterday UTC.

        Safe to call on every startup — only processes dates newer than last_date.
        Returns the (possibly updated) stats dict.
        """
        stats = self.load()
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        last_date = stats.get("last_date") or ""

        new_dates = [d for d in fill_ledger.all_dates() if last_date < d < today]
        if not new_dates:
            return stats

        for date_str in sorted(new_dates):
            fills = fill_ledger.read_date(date_str)
            day = stats["by_date"].setdefault(date_str, dict(self._ZERO_DATE))
            for rec in fills:
                cash_flow = rec.get("cash_flow", 0.0)
                rpnl = rec.get("realized_pnl", 0.0)
                token_id = rec.get("token_id", "")
                day["fills"] += 1
                day["cash_pnl"] += cash_flow
                day["realized_pnl"] += rpnl
                stats["total_fills"] += 1
                stats["total_cash_pnl"] += cash_flow
                stats["total_realized_pnl"] += rpnl
                if token_id:
                    mkt = stats["by_market"].setdefault(token_id, dict(self._ZERO_MARKET))
                    mkt["fills"] += 1
                    mkt["cash_pnl"] += cash_flow
                    mkt["realized_pnl"] += rpnl
                    mkt["question"] = rec.get("question", "") or mkt["question"]

        stats["last_date"] = max(new_dates)
        self._save(stats)
        log.info(
            f"Lifetime cache updated through {stats['last_date']}: "
            f"{stats['total_fills']} total fills"
        )
        return stats

    def rebuild(self, fill_ledger: "FillLedger") -> dict:
        """Full rebuild from all ledger files (excluding today). Overwrites existing cache."""
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        stats = {"last_date": "", "total_fills": 0, "total_realized_pnl": 0.0,
                 "total_cash_pnl": 0.0, "by_date": {}, "by_market": {}}

        all_past = sorted(d for d in fill_ledger.all_dates() if d < today)
        for date_str in all_past:
            fills = fill_ledger.read_date(date_str)
            day = stats["by_date"].setdefault(date_str, dict(self._ZERO_DATE))
            for rec in fills:
                cash_flow = rec.get("cash_flow", 0.0)
                rpnl = rec.get("realized_pnl", 0.0)
                token_id = rec.get("token_id", "")
                day["fills"] += 1
                day["cash_pnl"] += cash_flow
                day["realized_pnl"] += rpnl
                stats["total_fills"] += 1
                stats["total_cash_pnl"] += cash_flow
                stats["total_realized_pnl"] += rpnl
                if token_id:
                    mkt = stats["by_market"].setdefault(token_id, dict(self._ZERO_MARKET))
                    mkt["fills"] += 1
                    mkt["cash_pnl"] += cash_flow
                    mkt["realized_pnl"] += rpnl
                    mkt["question"] = rec.get("question", "") or mkt["question"]

        if all_past:
            stats["last_date"] = all_past[-1]
        self._save(stats)
        log.info(
            f"Lifetime cache rebuilt: {stats['total_fills']} fills across {len(all_past)} days"
        )
        return stats


class MakerStateLoader:
    """Loads persisted state into MakerState on startup.

    Call order:
      1. update lifetime cache with any new past-day fill files
      2. compute today_stats snapshot from today's fill ledger
      3. restore checkpoint (inventory, cash_pnl, realized, open_lots)
      4. replay any gap fills (written after last checkpoint, before shutdown)
    """

    def __init__(
        self,
        maker_state: "MakerState",
        fill_ledger: "FillLedger",
        base_dir: Path = _BASE,
        paper: bool = True,
    ):
        self._state = maker_state
        self._ledger = fill_ledger
        self._ckpt_path = Path(base_dir) / "maker_checkpoint.json"
        self._lifetime = LifetimeStatsCache(base_dir)
        self._paper = paper

    def load(self) -> None:
        """Full startup load. Must be called before any actor coroutines start."""
        # 1. Update lifetime cache (covers dates < today)
        lt_stats = self._lifetime.update_through_yesterday(self._ledger)
        self._state.lifetime_stats = lt_stats

        # 2. Pre-session today snapshot (all fills in today's file)
        self._state.today_stats = self._compute_today_stats()

        # 3. Restore inventory/P&L state from checkpoint
        if self._ckpt_path.exists():
            self._restore_checkpoint()

        # 4. Replay fills that were written after the last checkpoint
        self._replay_gap_fills()

        log.info(
            f"Maker state loaded — markets={len(self._state.inventory)} "
            f"fills={self._state.total_fills} "
            f"today_fills={self._state.today_stats.get('fills', 0)} "
            f"lifetime_fills={lt_stats.get('total_fills', 0)}"
        )

    def _compute_today_stats(self) -> dict:
        """Sum all fills in today's ledger file as a pre-session snapshot."""
        stats: dict = {"fills": 0, "cash_pnl": 0.0, "realized_pnl": 0.0, "by_market": {}}
        for rec in self._ledger.read_date():
            stats["fills"] += 1
            stats["cash_pnl"] += rec.get("cash_flow", 0.0)
            stats["realized_pnl"] += rec.get("realized_pnl", 0.0)
            tid = rec.get("token_id", "")
            if tid:
                mkt = stats["by_market"].setdefault(tid, {
                    "fills": 0, "cash_pnl": 0.0, "realized_pnl": 0.0, "question": ""
                })
                mkt["fills"] += 1
                mkt["cash_pnl"] += rec.get("cash_flow", 0.0)
                mkt["realized_pnl"] += rec.get("realized_pnl", 0.0)
                mkt["question"] = rec.get("question", "") or mkt["question"]
        return stats

    def _restore_checkpoint(self) -> None:
        try:
            data = json.loads(self._ckpt_path.read_text(encoding="utf-8"))
            s = self._state
            s.inventory = data.get("inventory", {})
            s.inventory_entry_time = data.get("inventory_entry_time", {})
            s.cash_pnl = data.get("cash_pnl", 0.0)
            s.realized_pnl = data.get("realized_pnl", 0.0)
            s.total_fills = data.get("total_fills", 0)
            s.total_cancels = data.get("total_cancels", 0)
            s.cooldowns = {k: v for k, v in data.get("cooldowns", {}).items() if v > time.time()}
            cutoff = time.time() - 3600.0
            s.inventory_cap_hits = {
                k: v for k, v in data.get("inventory_cap_hits", {}).items()
                if float(v.get("last_hit", 0.0) or 0.0) >= cutoff
            }
            s.daily_fills_seen = set(data.get("daily_fills_seen", []))
            s._open_lots = {
                k: [tuple(lot) for lot in v]
                for k, v in data.get("open_lots", {}).items()
            }
            log.info(
                f"Checkpoint restored: fills={s.total_fills} "
                f"inventory={len(s.inventory)} markets "
                f"fills_seen={len(s.daily_fills_seen)}"
            )
        except Exception as exc:
            log.warning(f"Could not restore checkpoint (starting fresh): {exc}")

    def _replay_gap_fills(self) -> None:
        """Apply fills from today's ledger that were written after the last checkpoint.

        Uses _track_session=False so these fills don't pollute session_by_market —
        they're already counted in today_stats (read at startup).
        """
        replayed = 0
        for rec in self._ledger.read_date():
            fill_id = rec.get("fill_id", "")
            if not fill_id or fill_id in self._state.daily_fills_seen:
                continue
            self._state.record_fill(
                token_id=rec["token_id"],
                side=rec["side"],
                price=rec["price"],
                size=rec["size"],
                filled_at=rec["ts"],
                question=rec.get("question", ""),
                _track_session=False,
            )
            self._state.daily_fills_seen.add(fill_id)
            replayed += 1
        if replayed:
            log.info(f"Replayed {replayed} gap fills from today's ledger")
