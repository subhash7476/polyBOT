import numpy as np
from dataclasses import dataclass, field
from utils.logger import get_logger

log = get_logger(__name__)

_LOG_ODDS_CLIP = 10.0  # clip log-odds to [-10, 10] → prob in [~0.00005, ~0.99995]


@dataclass
class Signal:
    name: str
    strength: float       # normalised [-1, +1]; +1 = strong YES, -1 = strong NO
    weight: float         # calibrated from fills.jsonl; start with config.SIGNAL_WEIGHTS
    confidence: float = 1.0  # 0–1; down-weights uncertain or stale signals


class BayesianEngine:
    """
    Logit-additive Bayesian engine.

    log_odds_final = log_odds_prior + Σ (weight_i × strength_i × confidence_i)

    Each signal's contribution is auditable. Weights are calibrated from resolved fills.
    Log-odds are clipped to [-10, 10] for numerical stability.
    """

    def __init__(self, prior: float = 0.5):
        assert 0 < prior < 1, f"prior must be in (0, 1), got {prior}"
        self._log_odds_prior = np.log(prior / (1 - prior))
        self._log_odds = float(self._log_odds_prior)
        self._signals: list[Signal] = []

    def reset(self, prior: float | None = None):
        if prior is not None:
            assert 0 < prior < 1
            self._log_odds_prior = np.log(prior / (1 - prior))
        self._log_odds = float(self._log_odds_prior)
        self._signals = []

    def add_signal(self, signal: Signal):
        increment = signal.weight * signal.strength * signal.confidence
        self._log_odds += increment
        self._signals.append(signal)
        log.debug(
            f"signal={signal.name} str={signal.strength:.3f} "
            f"w={signal.weight:.3f} conf={signal.confidence:.2f} Δ={increment:.4f}"
        )

    @property
    def probability(self) -> float:
        clipped = np.clip(self._log_odds, -_LOG_ODDS_CLIP, _LOG_ODDS_CLIP)
        return float(1 / (1 + np.exp(-clipped)))

    @property
    def signal_count(self) -> int:
        return len(self._signals)

    @property
    def active_signals(self) -> list[Signal]:
        return list(self._signals)

    def summary(self) -> dict:
        return {
            "prior": float(1 / (1 + np.exp(-self._log_odds_prior))),
            "posterior": self.probability,
            "log_odds": self._log_odds,
            "signals": [
                {"name": s.name, "strength": s.strength,
                 "weight": s.weight, "confidence": s.confidence}
                for s in self._signals
            ],
        }
