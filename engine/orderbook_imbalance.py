"""
engine/orderbook_imbalance.py

Order book imbalance signal.

Hypothesis: Persistent depth skew (bid_depth/ask_depth > OBI_RATIO_HIGH or
< OBI_RATIO_LOW) sustained over OBI_MIN_READINGS consecutive readings (15 min apart)
predicts short-term direction.

Low weight (0.10) — this signal is noisy but fast-updating.
"""
import math
import time
from collections import deque
from typing import Optional

from engine.bayesian import Signal
from market.state import ContractState
from config import OBI_RATIO_HIGH, OBI_RATIO_LOW, OBI_MIN_READINGS, SIGNAL_WEIGHTS
from utils.logger import get_logger

log = get_logger(__name__)

# Module-level OBI history: token_id -> deque of (unix_timestamp, ratio)
_obi_history: dict[str, deque] = {}


def record_obi_reading(token_id: str, contract_state: ContractState) -> None:
    """Record current bid/ask depth ratio. Call from trading loop."""
    if contract_state.ask_depth <= 0:
        return
    ratio = contract_state.bid_depth / contract_state.ask_depth
    if token_id not in _obi_history:
        _obi_history[token_id] = deque(maxlen=20)
    _obi_history[token_id].append((time.time(), ratio))


def compute_obi_signal(token_id: str) -> Optional[Signal]:
    """
    Returns Signal if OBI is sustained above/below threshold over recent readings.
    Returns None if insufficient history or not sustained.
    """
    history = _obi_history.get(token_id)
    if not history or len(history) < OBI_MIN_READINGS:
        return None

    # Check last OBI_MIN_READINGS readings for consistent direction
    recent = list(history)[-OBI_MIN_READINGS:]
    ratios = [r for _, r in recent]

    all_bullish = all(r > OBI_RATIO_HIGH for r in ratios)
    all_bearish = all(r < OBI_RATIO_LOW for r in ratios)

    if not all_bullish and not all_bearish:
        return None

    avg_ratio = sum(ratios) / len(ratios)
    raw_strength = math.log(avg_ratio) if avg_ratio > 0 else 0.0
    strength = max(-1.0, min(1.0, raw_strength))

    weight = SIGNAL_WEIGHTS.get("orderbook_imbalance", 0.10)

    log.debug(f"obi [{token_id[:8]}]: ratio={avg_ratio:.2f} strength={strength:.3f}")

    return Signal(
        name="orderbook_imbalance",
        strength=strength,
        weight=weight,
        confidence=0.8,
    )
