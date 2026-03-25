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


# TODO (deferred): Complement arb — requires tracking NO token best_ask independently
# from the YES token order book. Currently no_best_ask = 1 - yes_bid by definition,
# so YES ask + NO ask >= 1.0 always. Proper implementation needs ContractState.no_best_ask
# to be populated from the actual NO token CLOB feed, not derived. See discoveries.md.


@dataclass
class CrossTemporalViolation:
    earlier_token: str
    later_token: str
    asset: str
    target: float
    direction: str
    earlier_expiry_key: str
    later_expiry_key: str
    earlier_price: float
    later_price: float
    profit: float
    trade_description: str


def find_cross_temporal_violations(
    markets: list,
    min_profit: float = 0.01,
    fee_rate: float = 0.02,
) -> list:
    """
    Detect cross-temporal arbitrage: same asset/target/direction, different expiry.

    Rule for "above" direction:
    P(asset > target by t1) <= P(asset > target by t2) when t1 < t2.
    Violation: earlier-expiry market trades ABOVE later-expiry market.
    Trade: BUY NO on overpriced earlier, BUY YES on underpriced later.
    Net profit = (earlier_price - later_price) - 2 * fee_rate

    Rule for "below" direction:
    P(asset < target by t1) >= P(asset < target by t2) when t1 < t2.
    Violation: earlier-expiry market trades BELOW later-expiry market.
    """
    _EXPIRY_ORDER = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }

    def _expiry_sort_key(expiry_key: str) -> int:
        key_lower = expiry_key.lower()
        for month_str, month_num in _EXPIRY_ORDER.items():
            if key_lower.startswith(month_str):
                year_part = key_lower[len(month_str):]
                year = int(year_part) if year_part.isdigit() else 9999
                return year * 100 + month_num
        return 999999

    # Group by (asset, target, direction)
    groups: dict = defaultdict(list)
    for m in markets:
        key = f"{m.asset}_{m.target}_{m.direction}"
        groups[key].append(m)

    violations = []
    for group_key, group_markets in groups.items():
        if len(group_markets) < 2:
            continue

        sorted_markets = sorted(group_markets, key=lambda m: _expiry_sort_key(m.expiry_key))

        for i in range(len(sorted_markets)):
            for j in range(i + 1, len(sorted_markets)):
                earlier = sorted_markets[i]
                later = sorted_markets[j]

                if earlier.direction == "above":
                    # P(above by t_early) <= P(above by t_late) must hold
                    if earlier.yes_price > later.yes_price:
                        gross_profit = earlier.yes_price - later.yes_price
                        net_profit = gross_profit - 2 * fee_rate
                        if net_profit >= min_profit:
                            violations.append(CrossTemporalViolation(
                                earlier_token=earlier.token_id,
                                later_token=later.token_id,
                                asset=earlier.asset,
                                target=earlier.target,
                                direction=earlier.direction,
                                earlier_expiry_key=earlier.expiry_key,
                                later_expiry_key=later.expiry_key,
                                earlier_price=earlier.yes_price,
                                later_price=later.yes_price,
                                profit=net_profit,
                                trade_description=(
                                    f"BUY NO {earlier.asset}>${earlier.target:,.0f} "
                                    f"by {earlier.expiry_key} @ {1-earlier.yes_price:.3f}, "
                                    f"BUY YES {later.asset}>${later.target:,.0f} "
                                    f"by {later.expiry_key} @ {later.yes_price:.3f}"
                                ),
                            ))
                else:  # "below"
                    # P(below by t_early) >= P(below by t_late) must hold
                    if earlier.yes_price < later.yes_price:
                        gross_profit = later.yes_price - earlier.yes_price
                        net_profit = gross_profit - 2 * fee_rate
                        if net_profit >= min_profit:
                            violations.append(CrossTemporalViolation(
                                earlier_token=earlier.token_id,
                                later_token=later.token_id,
                                asset=earlier.asset,
                                target=earlier.target,
                                direction=earlier.direction,
                                earlier_expiry_key=earlier.expiry_key,
                                later_expiry_key=later.expiry_key,
                                earlier_price=earlier.yes_price,
                                later_price=later.yes_price,
                                profit=net_profit,
                                trade_description=(
                                    f"BUY YES {earlier.asset}<${earlier.target:,.0f} "
                                    f"by {earlier.expiry_key} @ {earlier.yes_price:.3f}, "
                                    f"BUY NO {later.asset}<${later.target:,.0f} "
                                    f"by {later.expiry_key} @ {1-later.yes_price:.3f}"
                                ),
                            ))

    violations.sort(key=lambda v: v.profit, reverse=True)
    return violations
