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
