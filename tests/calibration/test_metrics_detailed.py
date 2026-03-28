import json
import tempfile
import pytest
from pathlib import Path
from calibration.metrics import (
    load_resolved_fills,
    per_signal_attribution,
    category_breakdown,
    edge_decay_check,
    time_decay_analysis,
    strategy_type_breakdown,
    paper_validation_report,
)


def _write_fills(fills, path):
    path.write_text("\n".join(json.dumps(f) for f in fills))


def _fill(outcome, model_prob=0.65, market_prob=0.55, edge=0.10,
          category="crypto", signals=None):
    return {
        "outcome": outcome,
        "model_prob": model_prob,
        "market_prob": market_prob,
        "edge": edge,
        "signal_summary": {
            "signals": signals or [
                {"name": "vol_skew", "strength": 0.5, "weight": 0.15, "confidence": 1.0}
            ]
        },
        "category": category,
        "timestamp": "2026-03-01T00:00:00Z",
    }


def test_per_signal_attribution_returns_dict():
    fills = [_fill(1), _fill(0), _fill(1)]
    result = per_signal_attribution(fills)
    assert isinstance(result, dict)
    assert "vol_skew" in result
    attr = result["vol_skew"]
    assert "win_rate" in attr
    assert "count" in attr


def test_per_signal_attribution_win_rate():
    fills = [_fill(1), _fill(1), _fill(0)]
    result = per_signal_attribution(fills)
    assert result["vol_skew"]["win_rate"] == pytest.approx(2/3, rel=0.01)


def test_category_breakdown_groups_correctly():
    fills = [_fill(1, category="crypto"), _fill(0, category="rates"), _fill(1, category="crypto")]
    result = category_breakdown(fills)
    assert "crypto" in result
    assert "rates" in result
    assert result["crypto"]["count"] == 2
    assert result["rates"]["count"] == 1


def test_edge_decay_check_no_alert_when_healthy():
    fills = [_fill(i % 2, edge=0.05) for i in range(30)]
    alert = edge_decay_check(fills, window_days=30, min_edge=0.02)
    assert isinstance(alert, dict)
    assert "rolling_edge" in alert
    assert alert.get("alert") is False


def test_edge_decay_check_alerts_when_edge_low():
    fills = [_fill(i % 2, edge=0.005) for i in range(30)]
    alert = edge_decay_check(fills, window_days=30, min_edge=0.02)
    assert alert.get("alert") is True


def test_time_decay_analysis_returns_list():
    fills = [_fill(1) for _ in range(20)]
    result = time_decay_analysis(fills)
    assert isinstance(result, list)


def test_category_breakdown_includes_win_rate():
    fills = [
        _fill(1, category="crypto", market_prob=0.55),
        _fill(0, category="crypto", market_prob=0.55),
        _fill(1, category="weather", market_prob=0.60),
    ]
    result = category_breakdown(fills)
    assert "win_rate" in result["crypto"]
    assert "realized_pnl" in result["crypto"]
    assert "resolved_count" in result["crypto"]
    assert result["crypto"]["win_rate"] == pytest.approx(0.5)
    assert result["weather"]["win_rate"] == pytest.approx(1.0)


def test_strategy_type_breakdown():
    fills = [
        _fill(1, category="crypto"),
        _fill(0, category="weather"),
    ]
    fills[0]["strategy_type"] = "directional"
    fills[1]["strategy_type"] = "directional"
    result = strategy_type_breakdown(fills)
    assert "directional" in result
    assert result["directional"]["count"] == 2


def test_paper_validation_report_not_ready_with_no_fills():
    report = paper_validation_report([])
    assert report["ready"] is False
    assert report["checks"]["signals_50"]["pass"] is False
    assert report["checks"]["resolved_20"]["pass"] is False


def test_paper_validation_report_ready_when_all_pass():
    import random
    random.seed(42)
    fills = []
    for i in range(60):
        f = {
            "ts": f"2026-03-{(i % 28) + 1:02d}T12:00:00Z",
            "token_id": f"t{i}",
            "model_prob": 0.70,
            "market_prob": 0.55,
            "edge": 0.15,
            "ev": 0.10,
            "size_usdc": 20.0,
            "side": "BUY_YES",
            "category": "weather",
            "strategy_type": "directional",
            "outcome": (1 if i < 45 else None),  # 45 resolved, 15 unresolved
            "resolved_at": None,
        }
        fills.append(f)
    report = paper_validation_report(fills, bankroll=500.0)
    assert report["checks"]["signals_50"]["pass"] is True
    assert report["checks"]["resolved_20"]["pass"] is True
    assert report["checks"]["brier_lt_020"]["pass"] is True
    assert report["checks"]["mean_edge_gt_003"]["pass"] is True
    assert report["ready"] is True
