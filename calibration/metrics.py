import json
import numpy as np
from pathlib import Path
import argparse
from datetime import datetime, timezone, timedelta


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


def per_signal_attribution(fills: list[dict]) -> dict:
    """
    Per-signal win rate: when a signal was present with positive strength,
    what fraction of outcomes were wins?
    """
    from collections import defaultdict
    signal_stats: dict = defaultdict(lambda: {"wins": 0, "losses": 0})

    for f in fills:
        outcome = f.get("outcome")
        if outcome is None:
            continue
        signals = f.get("signal_summary", {}).get("signals", [])
        for s in signals:
            name = s.get("name", "unknown")
            strength = float(s.get("strength", 0))
            if strength > 0:
                if outcome == 1:
                    signal_stats[name]["wins"] += 1
                else:
                    signal_stats[name]["losses"] += 1

    result = {}
    for name, stats in signal_stats.items():
        total = stats["wins"] + stats["losses"]
        result[name] = {
            "win_rate": stats["wins"] / total if total else 0.0,
            "count": total,
            "wins": stats["wins"],
        }
    return result


def category_breakdown(fills: list[dict]) -> dict:
    """Brier score and mean edge per market category."""
    from collections import defaultdict
    groups: dict = defaultdict(list)
    for f in fills:
        cat = f.get("category", "unknown")
        groups[cat].append(f)

    result = {}
    for cat, cat_fills in groups.items():
        bs = brier_score(cat_fills)
        me = mean_edge(cat_fills)
        result[cat] = {
            "count": len(cat_fills),
            "brier_score": round(bs, 4),
            "mean_edge": round(me, 4),
        }
    return result


def edge_decay_check(
    fills: list[dict],
    window_days: int = 30,
    min_edge: float = 0.02,
) -> dict:
    """
    Compute rolling mean edge over last window_days.
    Alert if it falls below min_edge.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
    recent = [f for f in fills if f.get("timestamp", "") >= cutoff]
    rolling_edge = mean_edge(recent) if recent else 0.0
    alert = rolling_edge < min_edge and len(recent) >= 10
    return {
        "rolling_edge": round(rolling_edge, 4),
        "window_days": window_days,
        "min_edge_threshold": min_edge,
        "recent_count": len(recent),
        "alert": alert,
        "message": (
            f"ALERT: Rolling {window_days}d edge {rolling_edge:.3f} < {min_edge} threshold"
            if alert else "OK"
        ),
    }


def time_decay_analysis(fills: list[dict], bins: int = 4) -> list[dict]:
    """
    Check if signals degrade as markets approach resolution.
    Bins fills by days_to_expiry at signal time (weekly bins).
    Requires 'days_to_expiry' field — returns empty list if absent.
    """
    binned: dict = {}
    for f in fills:
        dte = f.get("days_to_expiry")
        if dte is None:
            continue
        bin_idx = min(int(dte / 7), bins - 1)
        if bin_idx not in binned:
            binned[bin_idx] = []
        binned[bin_idx].append(f)

    result = []
    for bin_idx in sorted(binned):
        bin_fills = binned[bin_idx]
        result.append({
            "week": bin_idx,
            "label": f"Week {bin_idx} before expiry",
            "count": len(bin_fills),
            "brier_score": round(brier_score(bin_fills), 4),
            "mean_edge": round(mean_edge(bin_fills), 4),
        })
    return result


def print_detailed_report(log_file: str = "fills.jsonl"):
    fills = load_resolved_fills(log_file)
    print_calibration_report(log_file)

    if not fills:
        return

    print("\n--- Per-Signal Attribution ---")
    attribution = per_signal_attribution(fills)
    for name, stats in sorted(attribution.items(), key=lambda x: -x[1]["count"]):
        print(f"  {name:<25} win_rate={stats['win_rate']:.2%} n={stats['count']}")

    print("\n--- Category Breakdown ---")
    for cat, stats in category_breakdown(fills).items():
        print(f"  {cat:<12} n={stats['count']} brier={stats['brier_score']} edge={stats['mean_edge']:.3f}")

    print("\n--- Edge Decay Check (30-day rolling) ---")
    decay = edge_decay_check(fills)
    print(f"  {decay['message']}  (n={decay['recent_count']})")

    print("\n--- Time Decay Analysis ---")
    for row in time_decay_analysis(fills):
        print(f"  {row['label']:<25} n={row['count']} brier={row['brier_score']} edge={row['mean_edge']:.3f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Calibration metrics")
    parser.add_argument("--detailed", action="store_true", help="Full breakdown report")
    parser.add_argument("--fills", default="fills.jsonl", help="Path to fills log")
    args = parser.parse_args()
    if args.detailed:
        print_detailed_report(args.fills)
    else:
        print_calibration_report(args.fills)
