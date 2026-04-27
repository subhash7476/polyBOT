"""maker/regime.py — Unified regime score for spread, size, and skew decisions."""

from dataclasses import dataclass
from math import copysign


# ── Category spread baseline ──────────────────────────────────────────────────
# Seeded from Becker (2026) maker-taker gap findings. Tune empirically after
# 500+ fills per category accumulate in fills_markout.jsonl.
CATEGORY_SPREAD_MULTIPLIER: dict[str, float] = {
    "finance":       1.0,
    "crypto":        1.2,
    "politics":      1.3,
    "sports":        1.6,
    "weather":       2.0,
    "entertainment": 2.2,
    "default":       1.5,
}

MIN_SPREAD = 0.005   # 0.5c floor
MAX_SPREAD = 0.15    # 15c ceiling


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


@dataclass(frozen=True)
class RegimeDecision:
    score: float              # 0.0-1.0 composite intensity
    spread_multiplier: float  # 1.0-2.5x dynamic factor (category baseline applied separately)
    size_multiplier: float    # 0.25-1.0x
    skew_adjustment: float    # +/-0-0.02, applied to fair_value BEFORE spread derivation
    flags: frozenset          # {"defensive", "unwind", "extreme", "toxic_flow"}


def compute_regime_score(
    vpin: float,
    markout_30s: float,
    inv_signed: float,
    hours_to_resolution: float,
    category: str,
) -> RegimeDecision:
    """Compute a unified regime decision from four market signals.

    Args:
        vpin: Size-weighted order flow imbalance 0-1. 0.5 = neutral/stale.
        markout_30s: Rolling avg post-fill price movement at T+30s. Negative = adverse.
        inv_signed: Net inventory as signed fraction of per-market cap (-1 to +1).
        hours_to_resolution: Hours until market resolves, clamped [0, 168].
        category: Market category string for flag context.

    Returns:
        RegimeDecision with spread_multiplier, size_multiplier, skew_adjustment, flags.
    """
    hours = max(0.0, hours_to_resolution)

    # Four score components
    vpin_score    = _clamp(abs(vpin - 0.5) / 0.3, 0.0, 1.0)
    markout_score = _clamp(-markout_30s / 0.05, 0.0, 1.0)
    inv_score     = abs(inv_signed)
    time_score    = _clamp((24.0 - hours) / 24.0, 0.0, 1.0) ** 1.5

    score = _clamp(
        0.35 * vpin_score
        + 0.25 * markout_score
        + 0.25 * inv_score
        + 0.15 * time_score,
        0.0, 1.0,
    )

    # Output derivation
    spread_multiplier = 1.0 + score * 1.5          # 1.0x to 2.5x
    size_multiplier   = max(0.25, 1.0 - 0.75 * score)

    # Skew nudges fair_value toward inventory reduction.
    # Small inventory = small skew. High score = stronger push. Cap +/-2c.
    abs_inv = abs(inv_signed)
    if abs_inv == 0.0:
        skew_adjustment = 0.0
    else:
        magnitude = min(0.02, 0.02 * abs_inv * (0.5 + score))
        skew_adjustment = -copysign(magnitude, inv_signed)

    # Flags
    flags: set[str] = set()
    if score > 0.4:
        flags.add("defensive")
    if score > 0.8:
        flags.add("extreme")
    if vpin_score > 0.7:
        flags.add("toxic_flow")
    if (abs_inv > 0.6
            and (markout_30s < -0.01 or hours < 4.0)):
        flags.add("unwind")

    return RegimeDecision(
        score=score,
        spread_multiplier=spread_multiplier,
        size_multiplier=size_multiplier,
        skew_adjustment=skew_adjustment,
        flags=frozenset(flags),
    )
