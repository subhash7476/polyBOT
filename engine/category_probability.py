"""Category-specific probability router for non-crypto markets.

This module keeps dedicated paths for finance, sports, politics, and event markets.
The current implementation uses:
  - finance: the existing asset/volatility model
  - sports / politics / event / election: a category-aware Bayesian anchor that can
    ingest optional external priors from FeedState.category_priors

The router is intentionally pluggable so new external data providers can feed in
category priors without changing the quote engine again.
"""

from __future__ import annotations

import math
from engine.bayesian import BayesianEngine, Signal
from engine.probability import build_model_probability
from engine.contract_parser import ParsedContract
from market.state import FeedState, ContractState


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _lookup_category_prior(
    feeds: FeedState,
    category: str,
    key: str,
) -> tuple[float | None, float, str]:
    """Return (probability, confidence, source) if an external category prior exists."""
    cat_map = getattr(feeds, "category_priors", {}) or {}
    by_cat = cat_map.get(category, {})
    prior = by_cat.get(key)
    if not prior:
        return None, 0.0, ""
    prob = prior.get("prob")
    conf = float(prior.get("confidence", 0.5))
    source = str(prior.get("source", "external"))
    if prob is None:
        return None, 0.0, ""
    return float(prob), _clamp(conf, 0.0, 1.0), source


def build_finance_probability(
    contract: ParsedContract,
    feeds: FeedState,
    weights: dict,
) -> tuple[float, int, BayesianEngine]:
    """Finance uses the existing asset/volatility model."""
    return build_model_probability(contract, feeds, weights)


def build_category_probability(
    contract: ParsedContract,
    contract_state: ContractState,
    feeds: FeedState,
    weights: dict,
) -> tuple[float, int, BayesianEngine]:
    """Dedicated path for sports / politics / event / election markets."""
    prior = _clamp(contract_state.mid, 0.05, 0.95)
    engine = BayesianEngine(prior=prior)

    # Always add a market-anchor signal so the signal filter has a category-specific
    # path instead of falling back to the generic microstructure-only branch.
    engine.add_signal(Signal(
        name=f"{contract.category}_market_anchor",
        strength=(prior - 0.5) * 2.0,
        weight=weights.get("category_market_anchor", 0.12),
        confidence=1.0,
    ))

    # Time-to-resolution is informative for sports/event/election markets and keeps
    # the model from treating long-dated and near-dated markets identically.
    hours = contract_state.hours_to_resolution
    time_strength = math.tanh((72.0 - hours) / 24.0)
    engine.add_signal(Signal(
        name=f"{contract.category}_time_decay",
        strength=time_strength,
        weight=weights.get("category_time_decay", 0.10),
        confidence=1.0,
    ))

    # Optional external prior hook. External provider modules can write
    # FeedState.category_priors[category][normalized_question or asset].
    key = (contract.asset or contract.question).strip().lower()
    external_prob, external_conf, source = _lookup_category_prior(feeds, contract.category, key)
    if external_prob is not None:
        engine.add_signal(Signal(
            name=f"{contract.category}_{source}",
            strength=_clamp((external_prob - 0.5) * 2.0, -1.0, 1.0),
            weight=weights.get("category_external_prior", 0.20),
            confidence=external_conf,
        ))

    return engine.probability, engine.signal_count, engine
