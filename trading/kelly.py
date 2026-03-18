import config
from utils.logger import get_logger

log = get_logger(__name__)

KELLY_MIN = 0.05    # 5% minimum fraction
KELLY_MAX = 0.10    # 10% maximum fraction — never exceed
HARD_CAP_USDC = 50  # never bet more than $50 until model is calibrated


def fractional_kelly(
    model_prob: float,
    market_price: float,
    bankroll: float,
    ev: float,
    signal_count: int,
    kelly_fraction: float | None = None,
) -> float:
    """
    Conservative Kelly sizing for uncalibrated model.
    - Fraction: 5–10% (KELLY_MIN to KELLY_MAX), never 25%
    - EV confidence: full size at 5% EV, half at 2.5%
    - Signal confidence: full size at 4+ signals
    - Hard cap: HARD_CAP_USDC until model is validated
    """
    if model_prob <= 0 or market_price <= 0:
        return 0.0

    p = model_prob
    q = 1 - p
    b = (1 - market_price) / market_price

    full_kelly = (p * b - q) / b
    if full_kelly <= 0:
        return 0.0

    fraction = kelly_fraction if kelly_fraction is not None else config.KELLY_FRACTION
    fraction = max(KELLY_MIN, min(KELLY_MAX, fraction))

    ev_confidence = min(1.0, ev / 0.05)          # full size at 5% EV
    signal_confidence = min(1.0, signal_count / 4)  # full size at 4+ signals

    raw_size = bankroll * full_kelly * fraction * ev_confidence * signal_confidence
    capped = min(raw_size, HARD_CAP_USDC)

    if capped < raw_size:
        log.info(f"kelly capped: ${raw_size:.2f} → ${capped:.2f}")

    return capped
