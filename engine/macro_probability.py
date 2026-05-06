"""
engine/macro_probability.py

Model probability for:
  - Macro contracts (CPI, unemployment, NFP, GDP): normal forecast error model.
  - Rate cut count markets ("Will N Fed rate cuts happen in 2026?"): Poisson model.
  - Single-meeting rate markets ("25 bps cut at April meeting?"): fed_may_cut_prob signal.

P(actual > target) = 1 - Φ((target - consensus) / σ_error)
P(exactly N cuts)  = Poisson(N, λ=fed_expected_cuts)
"""
import math
from scipy.stats import norm, poisson
from engine.bayesian import BayesianEngine, Signal
from engine.contract_parser import ParsedContract
from market.state import FeedState
from utils.logger import get_logger

log = get_logger(__name__)

# Historical forecast error std devs (empirical, from FRED research papers)
FORECAST_ERROR_STD = {
    "CPI": 0.15,           # CPI MoM typically ±0.15% from consensus
    "UNEMPLOYMENT": 0.10,  # unemployment rate ±0.1% from consensus
    "NFP": 50_000,         # nonfarm payrolls ±50k from consensus
    "GDP": 0.30,           # GDP growth ±0.3% from consensus
}

# FeedState attribute name per macro type
_CONSENSUS_FIELD = {
    "CPI": "consensus_cpi",
    "UNEMPLOYMENT": "consensus_unemployment",
    "NFP": "consensus_nfp",
    "GDP": "consensus_gdp",
}


def build_macro_probability(
    contract: ParsedContract,
    feeds: FeedState,
    weights: dict,
) -> tuple:
    """
    Build probability for macro-economic or rate contracts.
    Routes to the appropriate sub-model based on contract type.
    Returns (probability, signal_count, engine).
    """
    if contract.category == "rates":
        return _build_rate_probability(contract, feeds, weights)

    # --- Macro data-release contracts (CPI / GDP / NFP / unemployment) ---
    macro_type = contract.asset   # "CPI", "UNEMPLOYMENT", "NFP", "GDP"
    target = contract.target_price
    up = contract.direction == "above"

    consensus_field = _CONSENSUS_FIELD.get(macro_type)
    consensus = getattr(feeds, consensus_field, None) if consensus_field else None
    error_std = FORECAST_ERROR_STD.get(macro_type)

    if consensus is None or error_std is None or target is None:
        engine = BayesianEngine(prior=0.5)
        return 0.5, 0, engine

    z = (target - consensus) / error_std
    prob_above = 1.0 - float(norm.cdf(z))

    prior = prob_above if up else (1.0 - prob_above)
    prior = max(0.01, min(0.99, prior))
    engine = BayesianEngine(prior=prior)

    engine.add_signal(Signal(
        name="macro_consensus",
        strength=(0.5 - abs(prior - 0.5)) * 2,
        weight=weights.get("macro_consensus", 0.20),
    ))

    final_prob = float(max(0.01, min(0.99, engine.probability)))
    log.debug(f"macro {macro_type} consensus={consensus} target={target} prob={final_prob:.3f}")
    return final_prob, engine.signal_count, engine


def _build_rate_probability(
    contract: ParsedContract,
    feeds: FeedState,
    weights: dict,
) -> tuple:
    """
    Rate contract probability.

    direction == "exactly": Poisson model for "Will N cuts happen in 2026?"
    direction == "below":   single-meeting cut (uses fed_may_cut_prob)
    direction == "above":   single-meeting hike (1 - fed_may_cut_prob)
    direction == "hold":    1 - fed_may_cut_prob (approx)
    """
    engine = BayesianEngine(prior=0.5)

    if contract.direction in ("exactly", "above"):
        lam = feeds.fed_expected_cuts
        n = contract.cut_count

        if lam is None or n is None or lam <= 0:
            return 0.5, 0, engine

        # "exactly N" → PMF; "N or more" → survival function
        if contract.direction == "exactly":
            prior = float(poisson.pmf(n, lam))
        else:
            prior = float(1.0 - poisson.cdf(n - 1, lam))

        prior = max(0.01, min(0.99, prior))
        engine = BayesianEngine(prior=prior)

        engine.add_signal(Signal(
            name="poisson_cut_model",
            strength=(prior - 0.5) * 2,
            weight=weights.get("poisson_cut_model", 0.30),
        ))

        if feeds.sofr is not None:
            cuts_vs_n = lam - n   # positive = model expects more cuts than N
            engine.add_signal(Signal(
                name="sofr_cut_signal",
                strength=math.tanh(cuts_vs_n * 0.5),
                weight=weights.get("sofr_cut_signal", 0.15),
            ))

        final_prob = float(max(0.01, min(0.99, engine.probability)))
        log.debug(f"rate Poisson dir={contract.direction} N={n} lam={lam:.2f} prior={prior:.3f} final={final_prob:.3f}")
        return final_prob, engine.signal_count, engine

    # Single-meeting markets
    cut_prob = feeds.fed_may_cut_prob
    if cut_prob is None:
        return 0.5, 0, engine

    if contract.direction == "below":       # cut happened
        prior = max(0.01, min(0.99, cut_prob))
    elif contract.direction == "above":     # hike happened
        prior = max(0.01, min(0.99, 1.0 - cut_prob))
    else:                                   # hold
        prior = max(0.01, min(0.99, 1.0 - cut_prob))

    engine = BayesianEngine(prior=prior)
    engine.add_signal(Signal(
        name="fed_cut_prob",
        strength=(prior - 0.5) * 2,
        weight=weights.get("fed_cut_prob", 0.20),
        confidence=feeds.fed_confidence,
    ))

    final_prob = float(max(0.01, min(0.99, engine.probability)))
    return final_prob, engine.signal_count, engine
