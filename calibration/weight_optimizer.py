"""
calibration/weight_optimizer.py

Dynamic signal weight optimizer.

Reads fills.jsonl (resolved trades only), builds a feature matrix of
signal contributions, runs logistic regression via gradient descent, and
outputs weight recommendations for manual review.

Usage:
    python -m calibration.weight_optimizer [--fills fills.jsonl]

Output is recommendations only — does NOT auto-update config.py.
"""
import json
import argparse
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
from utils.logger import get_logger

log = get_logger(__name__)

MIN_SAMPLES_FOR_OPTIMIZATION = 100


@dataclass
class WeightRecommendation:
    signal_name: str
    current_weight: float
    recommended_weight: float
    n_samples: int
    confidence: str   # "high" | "medium" | "low" | "insufficient_data"
    note: str


def load_signal_matrix(log_file: str = "fills.jsonl") -> tuple[list, list, list]:
    """
    Parse fills.jsonl into (X, y, signal_names).
    X: list of dicts {signal_name: contribution} per fill
    y: list of outcomes (0 or 1)
    signal_names: sorted list of all signal names seen
    """
    path = Path(log_file)
    if not path.exists():
        return [], [], []

    rows = []
    outcomes = []
    all_signal_names: set = set()

    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        if record.get("outcome") is None:
            continue

        summary = record.get("signal_summary", {})
        signals = summary.get("signals", [])

        row = {}
        for s in signals:
            name = s.get("name", "")
            strength = float(s.get("strength", 0))
            weight = float(s.get("weight", 0))
            confidence = float(s.get("confidence", 1.0))
            contribution = weight * strength * confidence
            row[name] = contribution
            all_signal_names.add(name)

        rows.append(row)
        outcomes.append(int(record["outcome"]))

    signal_names = sorted(all_signal_names)
    X = [{name: row.get(name, 0.0) for name in signal_names} for row in rows]

    return X, outcomes, signal_names


def optimize_weights(log_file: str = "fills.jsonl") -> list[WeightRecommendation]:
    """
    Run logistic regression on signal contributions and return weight recommendations.
    """
    from config import SIGNAL_WEIGHTS

    X_dicts, y, signal_names = load_signal_matrix(log_file)
    n = len(X_dicts)

    if n == 0 or not signal_names:
        log.warning("no resolved fills found — cannot optimize weights")
        return []

    if n < MIN_SAMPLES_FOR_OPTIMIZATION:
        log.warning(f"only {n} resolved fills — need {MIN_SAMPLES_FOR_OPTIMIZATION} for reliable optimization")
        return [
            WeightRecommendation(
                signal_name=name,
                current_weight=SIGNAL_WEIGHTS.get(name, 0.0),
                recommended_weight=SIGNAL_WEIGHTS.get(name, 0.0),
                n_samples=n,
                confidence="insufficient_data",
                note=f"Need {MIN_SAMPLES_FOR_OPTIMIZATION - n} more resolved fills",
            )
            for name in signal_names
        ]

    X = np.array([[row[name] for name in signal_names] for row in X_dicts])
    y_arr = np.array(y, dtype=float)

    def sigmoid(z):
        return 1 / (1 + np.exp(-np.clip(z, -10, 10)))

    w = np.zeros(len(signal_names))
    lr = 0.1
    for _ in range(1000):
        preds = sigmoid(X @ w)
        grad = X.T @ (preds - y_arr) / n
        w -= lr * grad

    abs_w = np.abs(w)
    total = abs_w.sum()
    if total > 0:
        normalized = abs_w / total
        max_current = max(SIGNAL_WEIGHTS.values()) if SIGNAL_WEIGHTS else 0.3
        recommended = normalized * max_current * len(signal_names) * 0.5
    else:
        recommended = abs_w

    confidence = "high" if n >= 200 else "medium" if n >= 100 else "low"

    recommendations = []
    for i, name in enumerate(signal_names):
        current = SIGNAL_WEIGHTS.get(name, 0.0)
        rec_weight = round(float(recommended[i]), 3)
        delta_pct = abs(rec_weight - current) / max(current, 0.001) * 100
        note = ""
        if delta_pct > 50:
            note = f"Large change ({delta_pct:.0f}%) — review carefully"
        elif delta_pct < 5:
            note = "Current weight is close to optimal"

        recommendations.append(WeightRecommendation(
            signal_name=name,
            current_weight=current,
            recommended_weight=rec_weight,
            n_samples=n,
            confidence=confidence,
            note=note,
        ))

    return sorted(recommendations, key=lambda r: r.signal_name)


def print_recommendations(recommendations: list[WeightRecommendation]):
    print(f"\n{'='*60}")
    print(f"Signal Weight Optimization Report — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}")
    print(f"{'='*60}")
    if not recommendations:
        print("No recommendations — not enough data yet.")
        return
    n = recommendations[0].n_samples
    print(f"Resolved fills: {n} | Confidence: {recommendations[0].confidence}")
    print(f"\n{'Signal':<25} {'Current':>10} {'Recommended':>13} {'Delta':>8}  Note")
    print("-" * 70)
    for r in recommendations:
        delta = r.recommended_weight - r.current_weight
        delta_str = f"{delta:+.3f}"
        print(f"{r.signal_name:<25} {r.current_weight:>10.3f} {r.recommended_weight:>13.3f} {delta_str:>8}  {r.note}")
    print(f"\nDo NOT auto-apply. Review and update config.py manually.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Signal weight optimizer")
    parser.add_argument("--fills", default="fills.jsonl", help="Path to fills log")
    args = parser.parse_args()
    recs = optimize_weights(args.fills)
    print_recommendations(recs)
