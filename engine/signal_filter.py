from engine.bayesian import BayesianEngine
from utils.logger import get_logger

log = get_logger(__name__)

MIN_SIGNALS_REQUIRED = 2
MIN_SIGNAL_AGREEMENT = 0.60

_MICROSTRUCTURE_SIGNALS = frozenset({"flatline", "orderbook_imbalance", "volume_divergence"})


def passes_signal_filter(
    engine: BayesianEngine,
    min_signals: int = MIN_SIGNALS_REQUIRED,
) -> tuple[bool, str]:
    """
    Gate: do not trade on a single weak signal or when signals disagree.

    Checks:
    1. Minimum number of signals present (default 2)
    2. At least 60% of signals agree on direction (same sign of strength)

    Strong-prior exception: when the lognormal/Poisson prior is highly decisive
    (|prior - 0.5| * 2 >= 0.40), the prior itself constitutes the primary signal.
    In that case we require only that the majority direction agrees with the prior —
    tiny counter-signals (e.g. a near-zero funding rate) should not veto a model
    that says probability is ~1% or ~99%.
    """
    signals = engine.active_signals
    summary = engine.summary()
    prior_prob = summary.get("prior", 0.5)
    prior_confidence = abs(prior_prob - 0.5) * 2   # 0=neutral, 1=certain
    _strong_prior = prior_confidence >= 0.40

    if len(signals) < min_signals:
        # Allow when model prior is highly decisive and at least 1 signal is present
        if _strong_prior and len(signals) >= 1:
            pass   # fall through to directional check below
        else:
            # Microstructure-only exception: for election/event/generic markets
            # the prior IS the market price (confidence≈0), so the decisive-prior
            # exception never fires.  Allow passage when only microstructure
            # signals are present and they provide sufficient evidence.
            all_micro = len(signals) >= 1 and all(
                s.name in _MICROSTRUCTURE_SIGNALS for s in signals
            )
            if all_micro:
                pass   # fall through to microstructure-specific checks below
            else:
                return False, f"only {len(signals)} signal(s) (need {min_signals})"

    # Microstructure-only path: check rules specific to flatline / OBI / vol-div.
    all_micro = len(signals) >= 1 and all(
        s.name in _MICROSTRUCTURE_SIGNALS for s in signals
    )
    if all_micro:
        if len(signals) >= 2:
            micro_pos = sum(1 for s in signals if s.strength > 0)
            micro_neg = sum(1 for s in signals if s.strength < 0)
            micro_total = len(signals)
            micro_agreement = max(micro_pos, micro_neg) / micro_total if micro_total else 0.0
            if micro_agreement >= MIN_SIGNAL_AGREEMENT:
                return True, f"{len(signals)} microstructure signals, {micro_agreement:.0%} agreement"
            return False, (
                f"microstructure signals split {micro_pos}+ {micro_neg}- "
                f"({micro_agreement:.0%} agreement < {MIN_SIGNAL_AGREEMENT:.0%} required)"
            )
        # exactly 1 signal — only allow high-confidence flatline
        sole = signals[0]
        if sole.name == "flatline" and sole.confidence >= 0.70:
            return True, f"flatline signal, confidence={sole.confidence:.2f}"
        return False, (
            f"single microstructure signal '{sole.name}' insufficient "
            f"(confidence={sole.confidence:.2f})"
        )

    positive = sum(1 for s in signals if s.strength > 0)
    negative = sum(1 for s in signals if s.strength < 0)
    total = len(signals)
    agreement = max(positive, negative) / total if total else 0.0

    # Strong-prior exception: when prior is highly decisive, the dominant-direction
    # signal count just needs to be >= 1 (not a full 60% of all signals).
    # This prevents a tiny counter-signal from vetoing a near-certain lognormal prior.
    if _strong_prior:
        prior_direction_positive = prior_prob > 0.5
        prior_aligned = positive if prior_direction_positive else negative
        if prior_aligned >= 1:
            return True, (
                f"strong prior (conf={prior_confidence:.2f}) + "
                f"{prior_aligned} aligned signal(s) of {total}"
            )
        return False, (
            f"strong prior (conf={prior_confidence:.2f}) but 0 signals align with prior direction"
        )

    if agreement < MIN_SIGNAL_AGREEMENT:
        return False, (
            f"signals split {positive}+ {negative}- "
            f"({agreement:.0%} agreement < {MIN_SIGNAL_AGREEMENT:.0%} required)"
        )

    return True, f"{len(signals)} signals, {agreement:.0%} agreement"
