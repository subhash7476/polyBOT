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
    # Load all fills for total signal count (paper validation needs unresolved too)
    all_fills = []
    path = Path(log_file)
    if path.exists():
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            all_fills.append(json.loads(line))
    fills = [f for f in all_fills if f.get("outcome") is not None]
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
    print(f"{'=' * 50}")

    # Paper readiness summary
    report = paper_validation_report(all_fills)
    ready_str = "READY" if report["ready"] else "NOT READY"
    print(f"\nPaper Validation: {ready_str}")
    for key, check in report["checks"].items():
        status = "PASS" if check["pass"] else "FAIL"
        print(f"  [{status}] {key}")
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
    """Brier score, mean edge, win rate, realized P&L, and resolved count per market category."""
    from collections import defaultdict
    groups: dict = defaultdict(list)
    for f in fills:
        cat = f.get("category", "unknown")
        groups[cat].append(f)

    result = {}
    for cat, cat_fills in groups.items():
        resolved = [f for f in cat_fills if f.get("outcome") is not None]
        wins = 0
        total_pnl = 0.0
        for f in resolved:
            side = f.get("side", "BUY_YES")
            outcome = f["outcome"]
            mp = f.get("market_prob", 0.5)
            sz = f.get("size_usdc", 0.0)
            if side == "BUY_NO":
                won = (outcome == 0)
                entry = max(1.0 - mp, 0.01)
                payout = 1.0 - outcome
            else:
                won = (outcome == 1)
                entry = max(mp, 0.01)
                payout = float(outcome)
            if won:
                wins += 1
            total_pnl += (payout - entry) * (sz / entry)
        win_rate = wins / len(resolved) if resolved else 0.0
        result[cat] = {
            "count": len(cat_fills),
            "resolved_count": len(resolved),
            "brier_score": round(brier_score(cat_fills), 4),
            "mean_edge": round(mean_edge(cat_fills), 4),
            "win_rate": round(win_rate, 4),
            "realized_pnl": round(total_pnl, 2),
        }
    return result


def strategy_type_breakdown(fills: list[dict]) -> dict:
    """Same structure as category_breakdown but grouped by strategy_type."""
    from collections import defaultdict
    groups: dict = defaultdict(list)
    for f in fills:
        st = f.get("strategy_type", "directional")
        groups[st].append(f)

    result = {}
    for st, st_fills in groups.items():
        resolved = [f for f in st_fills if f.get("outcome") is not None]
        wins = 0
        total_pnl = 0.0
        for f in resolved:
            side = f.get("side", "BUY_YES")
            outcome = f["outcome"]
            mp = f.get("market_prob", 0.5)
            sz = f.get("size_usdc", 0.0)
            if side == "BUY_NO":
                won = (outcome == 0)
                entry = max(1.0 - mp, 0.01)
                payout = 1.0 - outcome
            else:
                won = (outcome == 1)
                entry = max(mp, 0.01)
                payout = float(outcome)
            if won:
                wins += 1
            total_pnl += (payout - entry) * (sz / entry)
        win_rate = wins / len(resolved) if resolved else 0.0
        result[st] = {
            "count": len(st_fills),
            "resolved_count": len(resolved),
            "brier_score": round(brier_score(st_fills), 4),
            "mean_edge": round(mean_edge(st_fills), 4),
            "win_rate": round(win_rate, 4),
            "realized_pnl": round(total_pnl, 2),
        }
    return result


def paper_validation_report(fills: list[dict], bankroll: float = 500.0) -> dict:
    """
    Returns pass/fail for the 5 CLAUDE.md paper-validation criteria:
    1. 50+ total signals
    2. 20+ resolved outcomes
    3. Brier score < 0.20
    4. Mean edge > 3%
    5. No single day with realized loss > 5% of bankroll

    Returns:
        {
            "ready": bool,          # True only if ALL 5 pass
            "checks": {
                "signals_50":       {"pass": bool, "value": int,   "required": 50},
                "resolved_20":      {"pass": bool, "value": int,   "required": 20},
                "brier_lt_020":     {"pass": bool, "value": float, "required": 0.20},
                "mean_edge_gt_003": {"pass": bool, "value": float, "required": 0.03},
                "no_bad_day":       {"pass": bool, "worst_day_loss": float, "limit": float},
            }
        }
    """
    import math
    from collections import defaultdict

    resolved = [f for f in fills if f.get("outcome") is not None]
    total_signals = len(fills)
    total_resolved = len(resolved)
    bs = brier_score(resolved) if resolved else float("nan")
    me = mean_edge(fills) if fills else 0.0

    # Per-day P&L check — group resolved fills by date
    daily_pnl: dict = defaultdict(float)
    for f in resolved:
        ts = f.get("ts") or f.get("timestamp", "")
        day = ts[:10] if ts else "unknown"
        side = f.get("side", "BUY_YES")
        outcome = f["outcome"]
        mp = f.get("market_prob", 0.5)
        sz = f.get("size_usdc", 0.0)
        if side == "BUY_NO":
            entry = max(1.0 - mp, 0.01)
            payout = 1.0 - outcome
        else:
            entry = max(mp, 0.01)
            payout = float(outcome)
        daily_pnl[day] += (payout - entry) * (sz / entry)

    max_daily_loss_limit = bankroll * 0.05
    worst_day = min(daily_pnl.values()) if daily_pnl else 0.0
    no_bad_day = worst_day >= -max_daily_loss_limit

    checks = {
        "signals_50":       {"pass": total_signals >= 50,  "value": total_signals,  "required": 50},
        "resolved_20":      {"pass": total_resolved >= 20, "value": total_resolved, "required": 20},
        "brier_lt_020":     {"pass": (not math.isnan(bs)) and bs < 0.20, "value": round(bs, 4) if not math.isnan(bs) else None, "required": 0.20},
        "mean_edge_gt_003": {"pass": me > 0.03,            "value": round(me, 4),   "required": 0.03},
        "no_bad_day":       {"pass": no_bad_day, "worst_day_loss": round(worst_day, 2), "limit": round(-max_daily_loss_limit, 2)},
    }
    return {
        "ready": all(c["pass"] for c in checks.values()),
        "checks": checks,
    }


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


def signal_strength_breakdown(fills: list[dict]) -> list[dict]:
    """
    Win rate bucketed by model_prob confidence level.
    Detects model overconfidence: if the 0.85+ bucket wins at the same rate
    as the 0.55-0.65 bucket, the model's confidence is not informative.
    """
    buckets = [
        ("0.55-0.65", 0.55, 0.65),
        ("0.65-0.75", 0.65, 0.75),
        ("0.75-0.85", 0.75, 0.85),
        ("0.85-1.00", 0.85, 1.01),
    ]
    resolved = [f for f in fills if f.get("outcome") is not None]
    result = []
    for label, lo, hi in buckets:
        # For BUY_YES: model_prob is the YES confidence.
        # For BUY_NO: effective confidence is 1 - model_prob.
        bucket_fills = []
        for f in resolved:
            side = f.get("side", "BUY_YES")
            mp = f.get("model_prob", 0.5)
            conf = mp if side == "BUY_YES" else (1 - mp)
            if lo <= conf < hi:
                bucket_fills.append(f)
        wins = sum(
            1 for f in bucket_fills
            if (f.get("side", "BUY_YES") == "BUY_YES" and f.get("outcome") == 1)
            or (f.get("side") == "BUY_NO" and f.get("outcome") == 0)
        )
        n = len(bucket_fills)
        result.append({
            "bucket": label,
            "count": n,
            "wins": wins,
            "win_rate": round(wins / n, 4) if n else None,
        })
    return result


def bootstrap_win_rate_ci(outcomes: list[int], n_simulations: int = 10_000) -> dict:
    """
    Bootstrap 95% confidence interval on win rate.
    outcomes: list of 1 (win) or 0 (loss)
    If ci_low > 0.50, edge is statistically real with 95% confidence.
    """
    if not outcomes:
        return {"mean": float("nan"), "ci_low": float("nan"), "ci_high": float("nan"), "n": 0}
    arr = np.array(outcomes)
    results = []
    rng = np.random.default_rng(42)
    for _ in range(n_simulations):
        sample = rng.choice(arr, size=len(arr), replace=True)
        results.append(sample.mean())
    lo, hi = np.percentile(results, [2.5, 97.5])
    return {"mean": float(arr.mean()), "ci_low": float(lo), "ci_high": float(hi), "n": len(arr)}


def print_detailed_report(log_file: str = "fills.jsonl"):
    # Load all fills for total signal count
    all_fills = []
    path = Path(log_file)
    if path.exists():
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            all_fills.append(json.loads(line))
    fills = [f for f in all_fills if f.get("outcome") is not None]
    print_calibration_report(log_file)

    if not fills:
        return

    print("\n--- Per-Signal Attribution ---")
    attribution = per_signal_attribution(fills)
    for name, stats in sorted(attribution.items(), key=lambda x: -x[1]["count"]):
        print(f"  {name:<25} win_rate={stats['win_rate']:.2%} n={stats['count']}")

    print("\n--- Category Breakdown ---")
    for cat, stats in category_breakdown(fills).items():
        print(
            f"  {cat:<12} n={stats['count']} resolved={stats['resolved_count']} "
            f"brier={stats['brier_score']} edge={stats['mean_edge']:.3f} "
            f"win_rate={stats['win_rate']:.2%} pnl=${stats['realized_pnl']:.2f}"
        )

    print("\n--- Strategy Type Breakdown ---")
    for st, stats in strategy_type_breakdown(fills).items():
        print(
            f"  {st:<25} n={stats['count']} resolved={stats['resolved_count']} "
            f"brier={stats['brier_score']} edge={stats['mean_edge']:.3f} "
            f"win_rate={stats['win_rate']:.2%} pnl=${stats['realized_pnl']:.2f}"
        )

    print("\n--- Signal Strength Breakdown (overconfidence check) ---")
    print("  If high-confidence buckets win at the same rate as low-confidence,")
    print("  the model's confidence is noise, not signal.")
    for row in signal_strength_breakdown(fills):
        if row["count"] == 0:
            print(f"  {row['bucket']}  n=0")
        else:
            flag = " ← CHECK" if row["win_rate"] is not None and row["win_rate"] < 0.50 else ""
            print(f"  {row['bucket']}  n={row['count']:3d}  win_rate={row['win_rate']:.2%}{flag}")

    print("\n--- Edge Decay Check (30-day rolling) ---")
    decay = edge_decay_check(fills)
    print(f"  {decay['message']}  (n={decay['recent_count']})")

    print("\n--- Time Decay Analysis ---")
    for row in time_decay_analysis(fills):
        print(f"  {row['label']:<25} n={row['count']} brier={row['brier_score']} edge={row['mean_edge']:.3f}")

    print("\n--- Paper Validation Report ---")
    report = paper_validation_report(all_fills)
    ready_str = "READY TO GO LIVE" if report["ready"] else "NOT READY"
    print(f"  Status: {ready_str}")
    for key, check in report["checks"].items():
        status = "PASS" if check["pass"] else "FAIL"
        print(f"  [{status}] {key}: {check}")

    print("\n--- Bootstrap Win Rate CI (95%) ---")
    outcomes = []
    for f in fills:
        side = f.get("side", "BUY_YES")
        outcome = f.get("outcome")
        if outcome is None:
            continue
        win = (side == "BUY_YES" and outcome == 1) or (side == "BUY_NO" and outcome == 0)
        outcomes.append(1 if win else 0)
    if len(outcomes) >= 10:
        ci = bootstrap_win_rate_ci(outcomes)
        edge_real = ci["ci_low"] > 0.50
        verdict = "EDGE REAL (95% CI lower bound > 50%)" if edge_real else "INSUFFICIENT EVIDENCE — lower bound ≤ 50%"
        print(f"  n={ci['n']}  win_rate={ci['mean']:.3f}  95% CI [{ci['ci_low']:.3f}, {ci['ci_high']:.3f}]")
        print(f"  Verdict: {verdict}")
    else:
        print(f"  n={len(outcomes)} — need ≥10 resolved trades for CI")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Calibration metrics")
    parser.add_argument("--detailed", action="store_true", help="Full breakdown report")
    parser.add_argument("--fills", default="fills.jsonl", help="Path to fills log")
    args = parser.parse_args()
    if args.detailed:
        print_detailed_report(args.fills)
    else:
        print_calibration_report(args.fills)
