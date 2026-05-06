"""
engine/flatline.py

Pre-resolution flatline detector.

Hypothesis: When a Polymarket YES price does not move by more than FLATLINE_THRESHOLD
over the past FLATLINE_WINDOW_HOURS, and the market resolves within FLATLINE_EXPIRY_GATE_HOURS,
the leading side wins ~79-81% of the time.

Signal: added to BayesianEngine with weight config.SIGNAL_WEIGHTS["flatline"].
"""
import time
from collections import deque, defaultdict
from datetime import datetime, timezone
from typing import Optional

from engine.bayesian import Signal
from engine.contract_parser import ParsedContract
from market.state import ContractState
from config import (
    FLATLINE_WINDOW_HOURS,
    FLATLINE_EXPIRY_GATE_HOURS,
    FLATLINE_THRESHOLD,
    FLATLINE_MIN_LEADING_PRICE,
    SIGNAL_WEIGHTS,
)
from utils.logger import get_logger

log = get_logger(__name__)

# Module-level price history: token_id -> deque of (unix_timestamp, yes_mid_price)
# Uses defaultdict so tests can write directly: _price_history["tok1"].append(...)
_price_history: defaultdict[str, deque] = defaultdict(lambda: deque(maxlen=200))


def record_price(token_id: str, yes_mid: float) -> None:
    """Append current YES mid price with timestamp. Call from trading loop."""
    _price_history[token_id].append((time.time(), yes_mid))


def compute_flatline_signal(
    token_id: str,
    contract_state: ContractState,
    parsed: ParsedContract,
) -> Optional[Signal]:
    """
    Returns a Signal if the market is in a pre-resolution flatline, else None.

    Flatline criteria:
    1. Market resolves within FLATLINE_EXPIRY_GATE_HOURS
    2. Leading YES price > FLATLINE_MIN_LEADING_PRICE
    3. Price range over last FLATLINE_WINDOW_HOURS < FLATLINE_THRESHOLD
    4. At least 5 price observations spanning the full window exist
    """
    if parsed.expiry is None:
        return None

    # Gate 1: close to expiry
    now = datetime.now(timezone.utc)
    hours_left = (parsed.expiry - now).total_seconds() / 3600
    if hours_left > FLATLINE_EXPIRY_GATE_HOURS or hours_left <= 0:
        return None

    yes_mid = contract_state.mid

    # Gate 2: leading side must be sufficiently priced in
    leading_price = max(yes_mid, 1 - yes_mid)
    if leading_price < FLATLINE_MIN_LEADING_PRICE:
        return None

    # Gate 3: enough history spanning the window
    history = _price_history.get(token_id)
    if not history or len(history) < 5:
        return None

    now_ts = time.time()
    window_start = now_ts - FLATLINE_WINDOW_HOURS * 3600
    window_prices = [p for ts, p in history if ts >= window_start]

    if len(window_prices) < 5:
        return None

    # Verify the observations span at least 90% of FLATLINE_WINDOW_HOURS
    oldest_in_window = min(ts for ts, _ in history if ts >= window_start)
    if now_ts - oldest_in_window < FLATLINE_WINDOW_HOURS * 3600 * 0.9:
        return None

    # Gate 4: price range check
    price_range = max(window_prices) - min(window_prices)
    if price_range >= FLATLINE_THRESHOLD:
        return None

    # Signal fires
    strength = (leading_price - 0.50) * 2  # maps [0.50, 1.0] -> [0, 1]
    confidence = max(0.1, 1.0 - price_range / FLATLINE_THRESHOLD)
    weight = SIGNAL_WEIGHTS.get("flatline", 0.20)

    log.info(
        f"flatline [{token_id[:8]}]: range={price_range:.4f} "
        f"leading={leading_price:.3f} hours_left={hours_left:.1f}h "
        f"strength={strength:.3f} conf={confidence:.2f}"
    )

    return Signal(
        name="flatline",
        strength=strength,
        weight=weight,
        confidence=confidence,
    )
