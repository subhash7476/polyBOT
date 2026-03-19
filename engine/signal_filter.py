from engine.bayesian import BayesianEngine
from utils.logger import get_logger

log = get_logger(__name__)

MIN_SIGNALS_REQUIRED = 2
MIN_SIGNAL_AGREEMENT = 0.60


def passes_signal_filter(
    engine: BayesianEngine,
    min_signals: int = MIN_SIGNALS_REQUIRED,
) -> tuple[bool, str]:
    """
    Gate: do not trade on a single weak signal or when signals disagree.

    Checks:
    1. Minimum number of signals present (default 2)
    2. At least 60% of signals agree on direction (same sign of strength)
    """
    signals = engine.active_signals
    if len(signals) < min_signals:
        # Allow 1 signal when the model prior is highly decisive (lognormal or Poisson gave
        # a strong prior, and at least 1 feed signal confirms the direction)
        prior_prob = engine.summary().get("prior", 0.5)
        prior_confidence = abs(prior_prob - 0.5) * 2   # 0=neutral, 1=certain
        if prior_confidence >= 0.40 and len(signals) >= 1:
            pass   # strong prior + 1 confirming signal is sufficient
        else:
            return False, f"only {len(signals)} signal(s) (need {min_signals})"

    positive = sum(1 for s in signals if s.strength > 0)
    negative = sum(1 for s in signals if s.strength < 0)
    total = len(signals)
    agreement = max(positive, negative) / total if total else 0.0

    if agreement < MIN_SIGNAL_AGREEMENT:
        return False, (
            f"signals split {positive}↑ {negative}↓ "
            f"({agreement:.0%} agreement < {MIN_SIGNAL_AGREEMENT:.0%} required)"
        )

    return True, f"{len(signals)} signals, {agreement:.0%} agreement"
