"""
engine/arb_scanner.py

Cross-market arbitrage detector.
Scans threshold markets for monotonicity violations.

Monotonicity rule: for "above" markets with the same asset+expiry:
  P(asset > low_strike) >= P(asset > high_strike)

Violation: high_strike YES price >= low_strike YES price (equal or inverted).
Trade: BUY YES on low_strike + BUY NO on high_strike.

No external signals needed — pure internal consistency check.
"""

from dataclasses import dataclass
from collections import defaultdict
from utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class ThresholdMarket:
    token_id: str
    asset: str            # "BTC", "SOL", etc.
    target: float         # strike price
    direction: str        # "above" or "below"
    expiry_key: str       # e.g. "mar2026" — used for grouping
    yes_price: float      # current YES price (mid)
    no_token_id: str


@dataclass
class ArbOpportunity:
    low_strike_token: str      # lower strike (should be MORE expensive for "above")
    high_strike_token: str     # higher strike (should be LESS expensive for "above")
    low_strike: float
    high_strike: float
    low_price: float
    high_price: float
    spread: float              # violation magnitude (high_price - low_price)
    asset: str
    expiry_key: str
    trade_description: str


def find_monotonicity_violations(
    markets: list,
    min_spread: float = 0.0,  # minimum spread to report (set >0 to filter near-zero violations)
) -> list:
    """
    Find pairs of same-asset, same-expiry, same-direction markets
    where prices violate monotonicity.

    For "above" direction:
      P(asset > low_strike) >= P(asset > high_strike) must hold.
      Violation: high_strike YES price >= low_strike YES price.
      Trade: BUY YES on low_strike + BUY NO on high_strike.

    Returns violations sorted by spread descending (best opportunities first).
    """
    # Group by (asset, direction, expiry_key)
    groups: dict = defaultdict(list)
    for m in markets:
        key = f"{m.asset}_{m.direction}_{m.expiry_key}"
        groups[key].append(m)

    violations = []

    for group_key, group_markets in groups.items():
        if len(group_markets) < 2:
            continue

        # Sort by strike price ascending
        sorted_markets = sorted(group_markets, key=lambda m: m.target)

        # Check all pairs for monotonicity violation
        for i in range(len(sorted_markets)):
            for j in range(i + 1, len(sorted_markets)):
                low = sorted_markets[i]   # lower strike
                high = sorted_markets[j]  # higher strike

                if low.direction == "above":
                    # P(above low_strike) should >= P(above high_strike)
                    # Violation: high_price >= low_price
                    if high.yes_price >= low.yes_price:
                        spread = high.yes_price - low.yes_price
                        if spread >= min_spread:
                            violations.append(ArbOpportunity(
                                low_strike_token=low.token_id,
                                high_strike_token=high.token_id,
                                low_strike=low.target,
                                high_strike=high.target,
                                low_price=low.yes_price,
                                high_price=high.yes_price,
                                spread=spread,
                                asset=low.asset,
                                expiry_key=low.expiry_key,
                                trade_description=(
                                    f"BUY YES {low.asset}>${low.target:,.0f} @ {low.yes_price:.3f}, "
                                    f"BUY NO {low.asset}>${high.target:,.0f} @ {1-high.yes_price:.3f}"
                                ),
                            ))

                else:  # "below"
                    # P(below low_strike) should <= P(below high_strike)
                    # Violation: low_price > high_price
                    if low.yes_price > high.yes_price:
                        spread = low.yes_price - high.yes_price
                        if spread >= min_spread:
                            violations.append(ArbOpportunity(
                                low_strike_token=low.token_id,
                                high_strike_token=high.token_id,
                                low_strike=low.target,
                                high_strike=high.target,
                                low_price=low.yes_price,
                                high_price=high.yes_price,
                                spread=spread,
                                asset=low.asset,
                                expiry_key=low.expiry_key,
                                trade_description=(
                                    f"BUY NO {low.asset}<${low.target:,.0f} @ {1-low.yes_price:.3f}, "
                                    f"BUY YES {low.asset}<${high.target:,.0f} @ {high.yes_price:.3f}"
                                ),
                            ))

    # Sort by spread descending (best opportunities first)
    violations.sort(key=lambda v: v.spread, reverse=True)
    return violations
