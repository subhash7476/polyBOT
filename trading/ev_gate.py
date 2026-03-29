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


def should_enter(
    ev: float,
    slippage: SlippageEstimate,
    ev_multiplier: float = 1.0,
) -> tuple[bool, str]:
    effective_threshold = config.MIN_EV_THRESHOLD * ev_multiplier
    if not slippage.tradeable:
        return False, "market too thin"
    if ev < effective_threshold:
        return False, f"EV {ev:.3f} < threshold {effective_threshold:.3f}"
    return True, f"EV={ev:.3f} slippage={slippage.slippage_pct:.2%}"
