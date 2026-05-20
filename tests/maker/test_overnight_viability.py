import time

from maker.overnight_viability import (
    OvernightViabilityThresholds,
    build_overnight_viability_report,
)


def test_overnight_viability_pending_before_min_hours():
    snapshot = {
        "paper": True,
        "session_fills": 120,
        "session_realized_pnl": 18.0,
        "session_mtm_pnl": 20.0,
        "today_fills": 120,
        "today_realized_pnl": 18.0,
        "quote_uptime": 0.42,
        "markout_stats": {"avg_markout_30s": 0.001},
        "active_markets": [{"rebate_eligible": True}],
        "selected_markets": [{"rebate_eligible": True}],
        "live_positions": [{"rebate_eligible": True}],
    }
    thresholds = OvernightViabilityThresholds(
        min_hours=8.0,
        min_session_fills=50,
        min_session_realized_pnl=5.0,
        min_session_markout_30s=-0.0015,
        min_rebate_eligible_active=1,
        min_rebate_eligible_ratio=0.1,
    )
    report = build_overnight_viability_report(snapshot, thresholds, started_at=time.time())

    assert report["status"] == "pending"
    assert report["pending"] is True
    assert report["pass"] is False
    assert "uptime_hours" in report["reasons"][0]


def test_overnight_viability_passes_when_thresholds_met():
    snapshot = {
        "paper": False,
        "session_fills": 140,
        "session_realized_pnl": 12.5,
        "session_mtm_pnl": 11.0,
        "today_fills": 140,
        "today_realized_pnl": 12.5,
        "quote_uptime": 0.55,
        "markout_stats": {"avg_markout_30s": 0.002},
        "active_markets": [
            {"rebate_eligible": True},
            {"rebate_eligible": True},
            {"rebate_eligible": False},
        ],
        "selected_markets": [{"rebate_eligible": True}],
        "live_positions": [{"rebate_eligible": True}],
    }
    thresholds = OvernightViabilityThresholds(
        min_hours=8.0,
        min_session_fills=100,
        min_session_realized_pnl=10.0,
        min_session_markout_30s=-0.0015,
        min_rebate_eligible_active=2,
        min_rebate_eligible_ratio=0.5,
    )
    started_at = time.time() - (9 * 3600)
    report = build_overnight_viability_report(snapshot, thresholds, started_at=started_at)

    assert report["status"] == "pass"
    assert report["pass"] is True
    assert report["fail"] is False
    assert report["reasons"] == []
