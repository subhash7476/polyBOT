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
    if contract.expiry is None or contract.target_price is None:
        engine = BayesianEngine(prior=0.5)
        return 0.5, 0, engine

    T = days_to_expiry(contract.expiry)
    up = contract.direction == "above"
    asset = contract.asset  # e.g. "BTC", "SOL", "XRP"

    # Generic per-asset lookup from FeedState dictionaries
    spot = feeds.spot_prices.get(asset)
    asset_dvol = feeds.dvol.get(asset)

    # Use lognormal as the analytical prior — anchors the model at the
    # correct baseline before any signal adjustments. Falls back to 0.5
    # if spot/dvol not yet available.
    if spot and asset_dvol and contract.target_price:
        lnorm_prob = lognormal_prob_above(spot, contract.target_price, asset_dvol / 100, T)
        prior = lnorm_prob if up else (1.0 - lnorm_prob)
        # Clamp away from 0/1 so log-odds remain finite
        prior = float(np.clip(prior, 0.01, 0.99))
        log.debug(f"lognormal prior={prior:.4f} spot={spot:.4g} target={contract.target_price} T={T:.1f}d dvol={asset_dvol:.1f}")
    else:
        prior = 0.5

    engine = BayesianEngine(prior=prior)

    # Signal 1: Volatility skew (per-asset)
    asset_skew = feeds.vol_skew.get(asset)
    if asset_skew is not None:
        skew_signal = -np.tanh(asset_skew / 10)
        engine.add_signal(Signal(
            name="vol_skew",
            strength=skew_signal if up else -skew_signal,
            weight=weights.get("vol_skew", 0.15),
        ))

    # Signal 2: Funding rate (per-asset; positive = crowded longs = mean-revert pressure)
    asset_funding = feeds.funding_rates.get(asset)
    if asset_funding is not None:
        fr_signal = -np.tanh(asset_funding * 1000)
        engine.add_signal(Signal(
            name="funding_rate",
            strength=fr_signal if up else -fr_signal,
            weight=weights.get("funding_rate", 0.15),
        ))

    # Signal 3: On-chain netflow (BTC-only for now; negative = outflows = bullish)
    if feeds.btc_exchange_netflow is not None and asset == "BTC":
        netflow_signal = -np.tanh(feeds.btc_exchange_netflow)
        engine.add_signal(Signal(
            name="onchain_netflow",
            strength=netflow_signal if up else -netflow_signal,
            weight=weights.get("onchain_netflow", 0.10),
        ))

    # Signal 4: DXY trend (rising dollar = crypto headwind)
    if feeds.dxy_trend is not None and feeds.dxy_confidence > 0:
        dxy_signal = -np.tanh(feeds.dxy_trend * 10)
        engine.add_signal(Signal(
            name="macro_dxy",
            strength=dxy_signal if up else -dxy_signal,
            weight=weights.get("macro_dxy", 0.10),
            confidence=feeds.dxy_confidence,
        ))

    # Signal 5: Fed cut probability (rates contracts only)
    if feeds.fed_may_cut_prob is not None and contract.category == "rates":
        engine.add_signal(Signal(
            name="fed_cut_prob",
            strength=(feeds.fed_may_cut_prob - 0.5) * 2,
            weight=weights.get("fed_cut_prob", 0.10),
            confidence=feeds.fed_confidence,
        ))

    # Signal 6: Stablecoin supply trend (slow-moving; applies to all crypto)
    if feeds.stablecoin_supply_change is not None and contract.category == "crypto":
        engine.add_signal(Signal(
            name="stablecoin_supply",
            strength=feeds.stablecoin_supply_change if up else -feeds.stablecoin_supply_change,
            weight=weights.get("stablecoin_supply", 0.05),
        ))

    # Signal 7: BTC hash rate trend (BTC-only; slow-moving)
    if feeds.btc_hashrate_trend is not None and asset == "BTC":
        engine.add_signal(Signal(
            name="btc_hashrate",
            strength=feeds.btc_hashrate_trend if up else -feeds.btc_hashrate_trend,
            weight=weights.get("btc_hashrate", 0.05),
        ))

    return engine.probability, engine.signal_count, engine


def build_microstructure_probability(
    contract: ParsedContract,
    contract_state,  # ContractState — use market mid as prior
    weights: dict,
) -> tuple[float, int, BayesianEngine]:
    """
    For markets without feed-based models (election, event, generic binary).
    Uses current market mid-price as the prior (crowd's estimate),
    then lets microstructure signals (flatline/OBI/VPD) adjust it.
    Signal filter will require microstructure signals to disagree with market
    before generating trades.
    """
    prior = float(np.clip(contract_state.mid, 0.05, 0.95))
    engine = BayesianEngine(prior=prior)
    return engine.probability, engine.signal_count, engine
