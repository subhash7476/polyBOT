import json
import tempfile
from pathlib import Path
from calibration.weight_optimizer import (
    load_signal_matrix,
    optimize_weights,
    WeightRecommendation,
)


def _write_fills(fills: list[dict], path: Path):
    path.write_text("\n".join(json.dumps(f) for f in fills))


def test_load_signal_matrix_empty():
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = Path(f.name)
    path.write_text("")
    X, y, names = load_signal_matrix(str(path))
    assert len(X) == 0
    assert len(y) == 0


def test_load_signal_matrix_filters_unresolved():
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = Path(f.name)
    fills = [
        {"outcome": 1, "model_prob": 0.7, "signal_summary": {
            "signals": [{"name": "vol_skew", "strength": 0.5, "weight": 0.15, "confidence": 1.0}]}},
        {"outcome": None, "model_prob": 0.6, "signal_summary": {
            "signals": [{"name": "vol_skew", "strength": 0.3, "weight": 0.15, "confidence": 1.0}]}},
    ]
    _write_fills(fills, path)
    X, y, names = load_signal_matrix(str(path))
    assert len(X) == 1
    assert len(y) == 1
    assert y[0] == 1


def test_optimize_weights_returns_recommendations():
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = Path(f.name)
    fills = []
    for i in range(20):
        fills.append({
            "outcome": 1 if i % 3 != 0 else 0,
            "model_prob": 0.65,
            "signal_summary": {"signals": [
                {"name": "vol_skew", "strength": 0.6 if i % 3 != 0 else -0.3,
                 "weight": 0.15, "confidence": 1.0},
                {"name": "funding_rate", "strength": 0.4 if i % 3 != 0 else -0.2,
                 "weight": 0.15, "confidence": 1.0},
            ]}
        })
    _write_fills(fills, path)
    recommendations = optimize_weights(str(path))
    assert isinstance(recommendations, list)


def test_weight_recommendation_structure():
    rec = WeightRecommendation(
        signal_name="vol_skew",
        current_weight=0.15,
        recommended_weight=0.22,
        n_samples=100,
        confidence="high",
        note="",
    )
    assert rec.signal_name == "vol_skew"
    assert rec.recommended_weight == 0.22


def test_optimize_requires_100_samples():
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = Path(f.name)
    fills = [
        {"outcome": i % 2, "model_prob": 0.6,
         "signal_summary": {"signals": [
             {"name": "vol_skew", "strength": 0.5, "weight": 0.15, "confidence": 1.0}
         ]}}
        for i in range(50)
    ]
    _write_fills(fills, path)
    recs = optimize_weights(str(path))
    for r in recs:
        assert r.confidence in ("low", "insufficient_data", "high", "medium")
