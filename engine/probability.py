"""
engine/probability.py

Converts FeedState + ParsedContract → model probability via logit-additive BayesianEngine.
Returns (prob, signal_count, engine) so callers can inspect signals and run signal_filter.

v2.1 Fix 2: engine is returned as third element so passes_signal_filter() can be wired
in main.py without reconstructing the engine.
"""

import numpy as np
from datetime import datetime, timezone
from scipy.stats import norm
from market.state import FeedState
from engine.bayesian import BayesianEngine, Signal
from engine.contract_parser import ParsedContract
from utils.logger import get_logger

log = get_logger(__name__)


def days_to_expiry(expiry_dt: datetime) -> float:
    """Days from now to expiry. Minimum 1/24 (1 hour)."""
    now = datetime.now(timezone.utc)
    delta = (expiry_dt - now).total_seconds() / 86400
    return max(delta, 1 / 24)


def lognormal_prob_above(spot: float, target: float,
                          sigma_annual: float, T_days: float) -> float:
    """
    P(S_T > target) under risk-neutral GBM, no drift (appropriate for prediction markets).
    sigma_T = sigma_annual / sqrt(252) * sqrt(T_days)
    d2 = ln(spot / target) / sigma_T
    P = N(d2)
    """
    if spot <= 0 or target <= 0 or sigma_annual <= 0:
        return 0.5
    sigma_daily = sigma_annual / np.sqrt(252)
    sigma_T = sigma_daily * np.sqrt(T_days)
    if sigma_T < 1e-6:
        return 1.0 if spot > target else 0.0
    d2 = np.log(spot / target) / sigma_T
    return float(norm.cdf(d2))


def build_model_probability(
    contract: ParsedContract,
    feeds: FeedState,
    weights: dict,
) -> tuple[float, int, BayesianEngine]:
    """
    Build model probability for a parsed contract.
    Returns (model_prob, signal_count, engine).
    engine is returned for signal_filter.passes_signal_filter() in main.py.
    """
    engine = BayesianEngine(prior=0.5)

    if contract.expiry is None or contract.target_price is None:
        return 0.5, 0, engine

    T = days_to_expiry(contract.expiry)
    up = contract.direction == "above"

    spot = feeds.btc_price if contract.asset == "BTC" else feeds.eth_price
    dvol = feeds.btc_dvol   if contract.asset == "BTC" else feeds.eth_dvol

    # Signal 1: DVOL log-normal probability
    if spot and dvol and contract.target_price:
        lnorm_prob = lognormal_prob_above(spot, contract.target_price, dvol / 100, T)
        strength = (lnorm_prob - 0.5) * 2
        if not up:
            strength = -strength
        # Confidence grows with time to expiry (more room to move)
        conf = min(1.0, T / 30)
        engine.add_signal(Signal(
            name="dvol_lognormal",
            strength=strength,
            weight=weights.get("dvol_lognormal", 0.30),
            confidence=conf,
        ))

    # Signal 2: Volatility skew
    if feeds.btc_vol_skew is not None and contract.asset == "BTC":
        skew_signal = -np.tanh(feeds.btc_vol_skew / 10)
        engine.add_signal(Signal(
            name="vol_skew",
            strength=skew_signal if up else -skew_signal,
            weight=weights.get("vol_skew", 0.15),
        ))

    # Signal 3: Funding rate (positive funding = crowded longs = mean-revert pressure)
    if feeds.btc_funding_rate is not None and contract.asset == "BTC":
        fr_signal = -np.tanh(feeds.btc_funding_rate * 1000)
        engine.add_signal(Signal(
            name="funding_rate",
            strength=fr_signal if up else -fr_signal,
            weight=weights.get("funding_rate", 0.15),
        ))

    # Signal 4: On-chain netflow (negative = outflows = bullish)
    if feeds.btc_exchange_netflow is not None:
        netflow_signal = -np.tanh(feeds.btc_exchange_netflow)
        engine.add_signal(Signal(
            name="onchain_netflow",
            strength=netflow_signal if up else -netflow_signal,
            weight=weights.get("onchain_netflow", 0.10),
        ))

    # Signal 5: DXY trend (rising dollar = crypto headwind)
    if feeds.dxy_trend is not None and feeds.dxy_confidence > 0:
        dxy_signal = -np.tanh(feeds.dxy_trend * 10)
        engine.add_signal(Signal(
            name="macro_dxy",
            strength=dxy_signal if up else -dxy_signal,
            weight=weights.get("macro_dxy", 0.10),
            confidence=feeds.dxy_confidence,
        ))

    # Signal 6: Fed cut probability (rates contracts only)
    if feeds.fed_may_cut_prob is not None and contract.category == "rates":
        engine.add_signal(Signal(
            name="fed_cut_prob",
            strength=(feeds.fed_may_cut_prob - 0.5) * 2,
            weight=weights.get("fed_cut_prob", 0.10),
            confidence=feeds.fed_confidence,
        ))

    return engine.probability, engine.signal_count, engine
