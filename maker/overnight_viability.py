"""Overnight viability monitor for the maker bot.

This is reporting-only. It does not stop the bot. It writes a restart-safe
JSON verdict to maker_data/overnight_viability.json so the next day can answer
whether the run met the configured hard thresholds.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import config
from maker.dashboard_state import MakerDashboardState
from utils.logger import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class OvernightViabilityThresholds:
    min_hours: float = config.OVERNIGHT_VIABILITY_MIN_HOURS
    min_session_fills: int = config.OVERNIGHT_VIABILITY_MIN_SESSION_FILLS
    min_session_realized_pnl: float = config.OVERNIGHT_VIABILITY_MIN_SESSION_REALIZED_PNL
    min_session_markout_30s: float = config.OVERNIGHT_VIABILITY_MIN_SESSION_MARKOUT_30S
    min_rebate_eligible_active: int = config.OVERNIGHT_VIABILITY_MIN_REBATE_ELIGIBLE_ACTIVE
    min_rebate_eligible_ratio: float = config.OVERNIGHT_VIABILITY_MIN_REBATE_ELIGIBLE_RATIO


def _eligible_counts(markets: list[dict[str, Any]] | None) -> tuple[int, float]:
    markets = markets or []
    if not markets:
        return 0, 0.0
    eligible = sum(1 for row in markets if bool(row.get("rebate_eligible")))
    return eligible, eligible / len(markets)


def build_overnight_viability_report(
    snapshot: dict[str, Any],
    thresholds: OvernightViabilityThresholds | None = None,
    started_at: float | None = None,
) -> dict[str, Any]:
    """Build a machine-readable pass/fail report from a dashboard snapshot."""
    thresholds = thresholds or OvernightViabilityThresholds()
    started_at = started_at or time.time()
    now = time.time()
    uptime_hours = max(0.0, (now - started_at) / 3600.0)

    markout_stats = snapshot.get("markout_stats", {}) or {}
    avg_markout_30s = float(markout_stats.get("avg_markout_30s", 0.0) or 0.0)

    active_markets = snapshot.get("active_markets", []) or []
    selected_markets = snapshot.get("selected_markets", []) or []
    live_positions = snapshot.get("live_positions", []) or []

    active_eligible, active_eligible_ratio = _eligible_counts(active_markets)
    selected_eligible, selected_eligible_ratio = _eligible_counts(selected_markets)
    live_eligible, live_eligible_ratio = _eligible_counts(live_positions)

    session_fills = int(snapshot.get("session_fills", 0) or 0)
    session_realized_pnl = float(snapshot.get("session_realized_pnl", 0.0) or 0.0)
    session_mtm_pnl = float(snapshot.get("session_mtm_pnl", 0.0) or 0.0)
    today_fills = int(snapshot.get("today_fills", 0) or 0)
    today_realized_pnl = float(snapshot.get("today_realized_pnl", 0.0) or 0.0)
    quote_uptime = float(snapshot.get("quote_uptime", 0.0) or 0.0)

    checks = {
        "min_hours": uptime_hours >= thresholds.min_hours,
        "min_session_fills": session_fills >= thresholds.min_session_fills,
        "min_session_realized_pnl": session_realized_pnl >= thresholds.min_session_realized_pnl,
        "min_session_markout_30s": avg_markout_30s >= thresholds.min_session_markout_30s,
        "min_rebate_eligible_active": active_eligible >= thresholds.min_rebate_eligible_active,
        "min_rebate_eligible_ratio": active_eligible_ratio >= thresholds.min_rebate_eligible_ratio,
    }

    reasons: list[str] = []
    status = "pending" if not checks["min_hours"] else "pass"
    if status == "pass":
        for key, ok in checks.items():
            if not ok:
                status = "fail"
                if key == "min_session_fills":
                    reasons.append(
                        f"session_fills={session_fills} < {thresholds.min_session_fills}"
                    )
                elif key == "min_session_realized_pnl":
                    reasons.append(
                        f"session_realized_pnl={session_realized_pnl:.2f} < {thresholds.min_session_realized_pnl:.2f}"
                    )
                elif key == "min_session_markout_30s":
                    reasons.append(
                        f"avg_markout_30s={avg_markout_30s:+.5f} < {thresholds.min_session_markout_30s:+.5f}"
                    )
                elif key == "min_rebate_eligible_active":
                    reasons.append(
                        f"rebate_eligible_active={active_eligible} < {thresholds.min_rebate_eligible_active}"
                    )
                elif key == "min_rebate_eligible_ratio":
                    reasons.append(
                        f"rebate_eligible_ratio={active_eligible_ratio:.3f} < {thresholds.min_rebate_eligible_ratio:.3f}"
                    )
    else:
        reasons.append(f"uptime_hours={uptime_hours:.2f} < {thresholds.min_hours:.2f}")

    return {
        "status": status,
        "pass": status == "pass",
        "fail": status == "fail",
        "pending": status == "pending",
        "generated_at": now,
        "started_at": started_at,
        "uptime_hours": round(uptime_hours, 3),
        "snapshot": {
            "paper": bool(snapshot.get("paper", True)),
            "session_fills": session_fills,
            "session_realized_pnl": round(session_realized_pnl, 4),
            "session_mtm_pnl": round(session_mtm_pnl, 4),
            "today_fills": today_fills,
            "today_realized_pnl": round(today_realized_pnl, 4),
            "quote_uptime": round(quote_uptime, 3),
            "avg_markout_30s": round(avg_markout_30s, 5),
            "active_markets": len(active_markets),
            "selected_markets": len(selected_markets),
            "live_positions": len(live_positions),
            "rebate_eligible_active": active_eligible,
            "rebate_eligible_active_ratio": round(active_eligible_ratio, 3),
            "rebate_eligible_selected": selected_eligible,
            "rebate_eligible_selected_ratio": round(selected_eligible_ratio, 3),
            "rebate_eligible_live": live_eligible,
            "rebate_eligible_live_ratio": round(live_eligible_ratio, 3),
        },
        "thresholds": asdict(thresholds),
        "checks": checks,
        "reasons": reasons,
    }


class OvernightViabilityMonitor:
    """Periodically writes a persisted hard-threshold report."""

    def __init__(
        self,
        maker_dash: MakerDashboardState,
        report_path: str = "maker_data/overnight_viability.json",
        interval: float = 300.0,
        thresholds: OvernightViabilityThresholds | None = None,
    ):
        self._dash = maker_dash
        self._path = Path(report_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._interval = interval
        self._thresholds = thresholds or OvernightViabilityThresholds()
        self._started_at = time.time()
        self._last_status: str | None = None

    def _snapshot(self) -> dict[str, Any]:
        return json.loads(self._dash.to_json())

    def _save(self, report: dict[str, Any]) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(report, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    async def run(self) -> None:
        while True:
            try:
                report = build_overnight_viability_report(
                    self._snapshot(),
                    thresholds=self._thresholds,
                    started_at=self._started_at,
                )
                self._save(report)
                if report["status"] != self._last_status:
                    log.info(
                        "Overnight viability %s: fills=%s realized=%+.2f markout30=%+.5f rebate_eligible=%s/%s",
                        report["status"].upper(),
                        report["snapshot"]["session_fills"],
                        report["snapshot"]["session_realized_pnl"],
                        report["snapshot"]["avg_markout_30s"],
                        report["snapshot"]["rebate_eligible_active"],
                        report["snapshot"]["active_markets"],
                    )
                    if report["reasons"]:
                        log.info("Overnight viability reasons: %s", "; ".join(report["reasons"]))
                    self._last_status = report["status"]
            except Exception as exc:
                log.warning(f"Overnight viability monitor error: {exc}")
            await asyncio.sleep(self._interval)
