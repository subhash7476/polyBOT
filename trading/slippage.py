from dataclasses import dataclass
from utils.logger import get_logger

log = get_logger(__name__)

_IMPACT_FACTOR = 0.10   # our order consumes ~10% of available liquidity (conservative)
_MAX_SLIPPAGE  = 0.10   # hard cap at 10%


@dataclass
class SlippageEstimate:
    adjusted_price: float   # effective entry price after slippage
    slippage_pct: float     # estimated slippage as fraction of price
    tradeable: bool         # False = market too thin for our size


def estimate_slippage(
    side: str,             # "BUY" or "SELL"
    size_usdc: float,
    best_bid: float,
    best_ask: float,
    volume_usd: float,
    max_slippage_pct: float = 0.02,
) -> SlippageEstimate:
    """
    Linear market-impact model.
    Slippage ≈ (size / volume) × IMPACT_FACTOR, capped at 10%.
    Rejects any market below $10k volume as untradeable regardless of size.
    """
    if volume_usd < 10_000:
        return SlippageEstimate(
            adjusted_price=best_ask if side == "BUY" else best_bid,
            slippage_pct=1.0,
            tradeable=False,
        )

    slippage_pct = min((size_usdc / volume_usd) * _IMPACT_FACTOR, _MAX_SLIPPAGE)

    if side == "BUY":
        adjusted_price = best_ask * (1 + slippage_pct)
    else:
        adjusted_price = best_bid * (1 - slippage_pct)

    tradeable = slippage_pct <= max_slippage_pct
    if not tradeable:
        log.info(f"skip: slippage {slippage_pct:.2%} > max {max_slippage_pct:.2%} "
                 f"for ${size_usdc:.0f} in ${volume_usd:.0f} market")

    return SlippageEstimate(
        adjusted_price=adjusted_price,
        slippage_pct=slippage_pct,
        tradeable=tradeable,
    )
