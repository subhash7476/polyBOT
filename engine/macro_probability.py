"""
engine/macro_probability.py

Model probability for macro-economic contracts (CPI, unemployment, NFP, GDP).
Uses consensus forecast + historical forecast error distribution (normal model).

P(actual > target) = 1 - Φ((target - consensus) / σ_error)

Free data sources wired via feeds/macro.py:
- FRED API (Federal Reserve Economic Data) — free with API key
"""
from scipy.stats import norm
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
    Build probability for macro-economic contracts.
    Returns (probability, signal_count, engine).
    """
    macro_type = contract.asset   # "CPI", "UNEMPLOYMENT", "NFP", "GDP"
    target = contract.target_price
    up = contract.direction == "above"

    consensus_field = _CONSENSUS_FIELD.get(macro_type)
    consensus = getattr(feeds, consensus_field, None) if consensus_field else None
    error_std = FORECAST_ERROR_STD.get(macro_type)

    if consensus is None or error_std is None or target is None:
        engine = BayesianEngine(prior=0.5)
        return 0.5, 0, engine

    # P(actual > target) using normal distribution of forecast errors
    z = (target - consensus) / error_std
    prob_above = 1.0 - float(norm.cdf(z))

    prior = prob_above if up else (1.0 - prob_above)
    prior = max(0.01, min(0.99, prior))
    engine = BayesianEngine(prior=prior)

    engine.add_signal(Signal(
        name="macro_consensus",
        strength=(0.5 - abs(prior - 0.5)) * 2,  # stronger when prior is decisive
        weight=weights.get("macro_consensus", 0.20),
    ))

    final_prob = float(max(0.01, min(0.99, engine.probability)))
    log.debug(f"macro {macro_type} consensus={consensus} target={target} prob={final_prob:.3f}")
    return final_prob, engine.signal_count, engine
