"""
engine/volume_divergence.py

Volume-price divergence signal.

Hypothesis: A volume spike (current > VPD_VOLUME_MULTIPLE * rolling avg) without
a corresponding price movement (< VPD_PRICE_MOVE_MAX) indicates accumulation.
Price tends to follow in the direction of the volume imbalance.

Signal strength: (volume / rolling_avg - 1) normalized, capped at 1.0.
Direction: inferred from price drift since first spike detection.
  - price > price_at_spike -> bullish (positive strength)
  - price < price_at_spike -> bearish (negative strength)
Weight: 0.10 (low confidence - direction ambiguous without trade tape).
"""
import time
from collections import deque
from typing import Optional

from engine.bayesian import Signal
from market.state import ContractState
from config import VPD_VOLUME_MULTIPLE, VPD_PRICE_MOVE_MAX, VPD_LOOKBACK_HOURS, SIGNAL_WEIGHTS
from utils.logger import get_logger

log = get_logger(__name__)

# token_id -> deque of (unix_timestamp, volume_usd_snapshot)
_volume_history: dict[str, deque] = {}
# token_id -> yes_mid at time of volume spike detection
_price_at_volume_spike: dict[str, float] = {}


def record_volume(token_id: str, contract_state: ContractState) -> None:
    """Record current volume snapshot. Call from trading loop each scan."""
    if token_id not in _volume_history:
        _volume_history[token_id] = deque(maxlen=100)
    _volume_history[token_id].append((time.time(), contract_state.volume_usd))


def compute_vpd_signal(
    token_id: str,
    contract_state: ContractState,
) -> Optional[Signal]:
    """
    Returns Signal if there is a volume spike without price confirmation.
    Returns None if conditions not met.
    """
    history = _volume_history.get(token_id)
    if not history or len(history) < 10:
        return None

    now_ts = time.time()
    window_start = now_ts - VPD_LOOKBACK_HOURS * 3600
    window_vols = [v for ts, v in history if ts >= window_start]

    if len(window_vols) < 5:
        return None

    rolling_avg = sum(window_vols) / len(window_vols)
    if rolling_avg <= 0:
        return None

    current_vol = contract_state.volume_usd
    volume_multiple = current_vol / rolling_avg

    if volume_multiple < VPD_VOLUME_MULTIPLE:
        return None  # not a spike

    yes_mid = contract_state.mid

    # Record price at first detection of this spike
    if token_id not in _price_at_volume_spike:
        _price_at_volume_spike[token_id] = yes_mid
        return None  # first detection - record but don't signal yet

    price_at_spike = _price_at_volume_spike[token_id]
    price_move = abs(yes_mid - price_at_spike)

    if price_move >= VPD_PRICE_MOVE_MAX:
        # Price has now moved - clear the spike record
        _price_at_volume_spike.pop(token_id, None)
        return None  # price followed volume, divergence resolved

    # Volume spike + no price move = divergence signal
    raw_magnitude = min(1.0, (volume_multiple - VPD_VOLUME_MULTIPLE) / VPD_VOLUME_MULTIPLE)
    magnitude = max(0.05, raw_magnitude)

    # Direction: infer from price drift since spike was first detected
    price_direction = 1.0 if yes_mid >= price_at_spike else -1.0
    strength = price_direction * magnitude

    weight = SIGNAL_WEIGHTS.get("volume_divergence", 0.10)

    log.debug(
        f"vpd [{token_id[:8]}]: vol_mult={volume_multiple:.1f}x "
        f"price_move={price_move:.4f} strength={strength:.3f}"
    )

    return Signal(
        name="volume_divergence",
        strength=strength,
        weight=weight,
        confidence=0.6,
    )
