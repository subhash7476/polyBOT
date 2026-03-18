import json
import numpy as np
from pathlib import Path


def load_resolved_fills(log_file: str = "fills.jsonl") -> list[dict]:
    """Load only fills where outcome is known (not None)."""
    fills = []
    path = Path(log_file)
    if not path.exists():
        return fills
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("outcome") is not None:
            fills.append(r)
    return fills


def brier_score(fills: list[dict]) -> float:
    """Mean squared error between model_prob and outcome. Lower = better. Random = 0.25."""
    if not fills:
        return float("nan")
    errors = [(f["model_prob"] - f["outcome"]) ** 2 for f in fills]
    return float(np.mean(errors))


def calibration_curve(fills: list[dict], bins: int = 10) -> list[dict]:
    """
    Group predictions into bins; compare predicted vs actual frequency.
    Perfect calibration: when model says 60%, actual outcome rate = 60%.
    """
    if not fills:
        return []
    probs = np.array([f["model_prob"] for f in fills])
    outcomes = np.array([f["outcome"] for f in fills])
    bin_edges = np.linspace(0, 1, bins + 1)
    result = []
    for i in range(bins):
        mask = (probs >= bin_edges[i]) & (probs < bin_edges[i + 1])
        if mask.sum() == 0:
            continue
        result.append({
            "bin_center": round((bin_edges[i] + bin_edges[i + 1]) / 2, 2),
            "predicted_mean": round(float(probs[mask].mean()), 3),
            "actual_rate": round(float(outcomes[mask].mean()), 3),
            "count": int(mask.sum()),
        })
    return result


def mean_edge(fills: list[dict]) -> float:
    if not fills:
        return 0.0
    return float(np.mean([f["edge"] for f in fills]))


def print_calibration_report(log_file: str = "fills.jsonl"):
    fills = load_resolved_fills(log_file)
    if not fills:
        print("No resolved fills yet.")
        return
    print(f"\n{'=' * 50}")
    print(f"Calibration Report — {len(fills)} resolved fills")
    print(f"Brier Score:  {brier_score(fills):.4f}  (0=perfect, 0.25=random)")
    print(f"Mean Edge:    {mean_edge(fills):.3f}")
    print(f"\nCalibration Curve:")
    for row in calibration_curve(fills):
        ok = "✓" if abs(row["predicted_mean"] - row["actual_rate"]) < 0.05 else "✗"
        print(f"  {ok} pred={row['predicted_mean']:.2f} actual={row['actual_rate']:.2f} n={row['count']}")
    print(f"{'=' * 50}\n")
