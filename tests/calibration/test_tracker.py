import json
import pytest
from pathlib import Path
from calibration.tracker import CalibrationTracker
from calibration.metrics import load_resolved_fills, brier_score, mean_edge, calibration_curve


@pytest.fixture
def tracker(tmp_path):
    return CalibrationTracker(log_file=str(tmp_path / "fills.jsonl"))


def test_log_signal_writes_jsonl(tracker, tmp_path):
    tracker.log_signal("t1", 0.65, 0.50, {"count": 2}, 50.0, 0.05)
    lines = (tmp_path / "fills.jsonl").read_text().strip().split("\n")
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["token_id"] == "t1"
    assert record["outcome"] is None
    assert "ts" in record


def test_multiple_signals_append(tracker, tmp_path):
    for i in range(3):
        tracker.log_signal(f"t{i}", 0.60, 0.50, {}, 50.0, 0.04)
    lines = (tmp_path / "fills.jsonl").read_text().strip().split("\n")
    assert len(lines) == 3


def test_record_outcome_updates_correct_record(tracker, tmp_path):
    tracker.log_signal("t1", 0.65, 0.50, {}, 50.0, 0.05)
    tracker.log_signal("t2", 0.40, 0.50, {}, 50.0, 0.04)
    tracker.record_outcome("t1", resolved_yes=True)
    lines = (tmp_path / "fills.jsonl").read_text().strip().split("\n")
    t1 = json.loads(lines[0])
    t2 = json.loads(lines[1])
    assert t1["outcome"] == 1
    assert t2["outcome"] is None


def test_brier_score_perfect():
    fills = [{"model_prob": 1.0, "outcome": 1}, {"model_prob": 0.0, "outcome": 0}]
    assert brier_score(fills) == 0.0


def test_brier_score_random():
    fills = [{"model_prob": 0.5, "outcome": 1}, {"model_prob": 0.5, "outcome": 0}]
    assert abs(brier_score(fills) - 0.25) < 1e-9


def test_brier_score_empty():
    import math
    assert math.isnan(brier_score([]))


def test_mean_edge():
    fills = [{"edge": 0.10}, {"edge": 0.20}]
    assert abs(mean_edge(fills) - 0.15) < 1e-9


def test_calibration_curve_groups_correctly():
    fills = [
        {"model_prob": 0.55, "outcome": 1},
        {"model_prob": 0.65, "outcome": 0},
    ]
    curve = calibration_curve(fills, bins=5)
    assert len(curve) >= 1
    assert "predicted_mean" in curve[0]
    assert "actual_rate" in curve[0]
