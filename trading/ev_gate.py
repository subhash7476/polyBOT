"""
trading/ev_gate.py

v2.1 Fix 1: get_trade_direction — BUY_YES when model > market, BUY_NO when model < market.
v2.1 Fix 4: calculate_ev includes spread_penalty + ADVERSE_SELECTION_PENALTY.
"""

import config
from trading.slippage import SlippageEstimate
from utils.logger import get_logger

log = get_logger(__name__)

POLYMARKET_FEE = config.POLYMARKET_FEE
ADVERSE_SELECTION_PENALTY = config.ADVERSE_SELECTION_PENALTY


def get_trade_direction(
    model_prob: float, market_price: float
) -> tuple[str, float]:
    """
    BUY YES when model probability > market price (underpriced YES).
    BUY NO  when model probability < market price (overpriced YES = underpriced NO).
    Returns (side, relevant_market_price_for_that_side).
    """
    yes_ev = model_prob - market_price
    no_ev  = (1 - model_prob) - (1 - market_price)   # = market_price - model_prob

    if yes_ev >= no_ev:
        return "BUY_YES", market_price
    else:
        return "BUY_NO", 1 - market_price  # NO ask = 1 - YES bid


def calculate_ev(
    model_prob: float,
    market_price: float,
    slippage: SlippageEstimate,
    payout: float = 1.0,
    ev_multiplier: float = 1.0,
    fees_enabled: bool = True,
) -> tuple[float, str]:
    """
    EV with slippage-adjusted entry price + spread penalty + adverse selection penalty.
    Returns (ev, side).
    fees_enabled=False for negRisk weather markets (feesEnabled=False on Polymarket).
    """
    side, _ = get_trade_direction(model_prob, market_price)
    effective_prob = model_prob if side == "BUY_YES" else (1 - model_prob)

    fee = POLYMARKET_FEE if fees_enabled else 0.0
    cost = (
        slippage.adjusted_price * (1 + fee)
        + ADVERSE_SELECTION_PENALTY
    )
    ev = (effective_prob * payout) - cost
    return ev, side


def passes_divergence_guard(
    model_prob: float,
    market_price: float,
    signal_count: int,
    category: str,
) -> tuple[bool, str]:
    """
    Reject weather trades where model and market disagree by >35 percentage points
    and signal diversity is low (fewer than 2 independent signals).

    A >35pp gap with a single signal path almost certainly means the model is
    wrong, not the market.  The market aggregates many forecasters; a single
    uncalibrated ECMWF run should not override it at high confidence.
    """
    if category != "weather":
        return True, "ok"
    divergence = abs(model_prob - market_price)
    if divergence > 0.35 and signal_count < 2:
        return False, (
            f"weather divergence guard: |model={model_prob:.3f} - market={market_price:.3f}| "
            f"= {divergence:.3f} > 0.35 with only {signal_count} signal(s)"
        )
    return True, "ok"


def should_enter(
    ev: float,
    slippage: SlippageEstimate,
    ev_multiplier: float = 1.0,
) -> tuple[bool, str]:
    effective_threshold = config.MIN_EV_THRESHOLD * ev_multiplier
    if not slippage.tradeable:
        return False, "market too thin"
    # Reject near-resolved markets: EV math produces nonsensical numbers when
    # buying at sub-penny prices (e.g. NO at 0.15¢ gives 49× EV on any model
    # disagreement, even though the market has effectively already resolved).
    if slippage.adjusted_price < config.MIN_ENTRY_PRICE:
        return False, f"entry price {slippage.adjusted_price:.4f} < floor {config.MIN_ENTRY_PRICE}"
    if ev < effective_threshold:
        return False, f"EV {ev:.3f} < threshold {effective_threshold:.3f}"
    return True, f"EV={ev:.3f} slippage={slippage.slippage_pct:.2%}"
