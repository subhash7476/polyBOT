"""Shared mutable state for the maker bot."""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from maker.types import QuoteIntent
from config import MAKER_MAX_INVENTORY_PER_MARKET, MAKER_MAX_TOTAL_INVENTORY, MAKER_CATEGORY_CAPS


@dataclass
class MakerState:
    """Thread-safe state shared across all maker actors."""

    max_inventory_per_market: float = field(default_factory=lambda: MAKER_MAX_INVENTORY_PER_MARKET)
    max_total_inventory: float = field(default_factory=lambda: MAKER_MAX_TOTAL_INVENTORY)

    # Per-category cap overrides — built from env vars in config.py.
    # Weather always present; other categories included when their env var is non-zero.
    # Missing categories fall back to max_inventory_per_market.
    category_inventory_caps: dict = field(default_factory=lambda: dict(MAKER_CATEGORY_CAPS))

    # Per-market net position: positive = holding YES, negative = holding NO
    inventory: dict[str, float] = field(default_factory=dict)

    # Unix timestamp when each market first received non-zero inventory this session.
    # Used by _expire_paper_positions to age-out markets absent from app_state.
    inventory_entry_time: dict[str, float] = field(default_factory=dict)

    # Markets in reduce-only mode: position must shrink before new exposure allowed.
    # Populated by MarketSelector when a market exits selection with open inventory.
    reduce_only_markets: set = field(default_factory=set)

    # Per-market live order IDs: token_id → {"bid_order_id": str, "ask_order_id": str, "bid_price": float, ...}
    live_orders: dict[str, list[dict]] = field(default_factory=dict)

    # Currently selected token_ids (written by MarketSelector after each selection cycle)
    selected_token_ids: set = field(default_factory=set)

    # Per-market last emitted quote (for stale detection)
    last_quotes: dict[str, QuoteIntent] = field(default_factory=dict)

    # Per-market rolling markouts: token_id -> {interval: avg_markout}
    rolling_markouts: dict[str, dict[int, float]] = field(default_factory=dict)

    # Per-market cooldown: token_id -> resume_at (unix timestamp)
    cooldowns: dict[str, float] = field(default_factory=dict)

    # Per-market inventory-cap hit tracking used for escalating cooldowns.
    # token_id -> {"count": int, "last_hit": unix timestamp}
    inventory_cap_hits: dict[str, dict] = field(default_factory=dict)

    # Global cooldown: set when total inventory cap fires; blocks ALL quoting
    global_cooldown_until: float = 0.0

    # Cash-flow P&L: sum of (SELL price*size) - (BUY price*size) across all fills.
    # WARNING: does NOT account for open position value. Use mtm_pnl() for real P&L.
    cash_pnl: float = 0.0

    # Realized P&L: booked profit from completed round trips (FIFO lot matching).
    # Only moves when a fill closes an existing opposite-side position.
    realized_pnl: float = 0.0

    # FIFO lot queue per token: list of [side, price, remaining_size]
    _open_lots: dict = field(default_factory=dict)

    # Fill and cancel counters for cancel-to-fill ratio
    total_fills: int = 0
    total_cancels: int = 0

    # Alias kept for any code that still reads daily_pnl
    @property
    def daily_pnl(self) -> float:
        return self.cash_pnl

    # Fill history for dashboard (capped at 100)
    fill_history: list = field(default_factory=list)

    # Fill timestamps for rapid-fill detection: token_id → {side → timestamp}
    last_fill_times: dict[str, dict[str, float]] = field(default_factory=dict)

    # Recent fill directions for adverse-selection detection: token_id → deque of "BUY"/"SELL"
    recent_fill_sides: dict[str, list] = field(default_factory=dict)

    # Persistence: unique ID for this runtime session
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    # Persistence: fill_ids from today's ledger that have already been applied to inventory
    daily_fills_seen: set = field(default_factory=set)

    # Persistence: per-market P&L for fills since last UTC midnight (not replayed).
    # token_id → {fills, cash_pnl, realized_pnl, question}
    # Cleared at UTC midnight so it tracks only today's fills (used for "Today" P&L).
    session_by_market: dict = field(default_factory=dict)

    # Cumulative session totals folded in at each UTC midnight rollover.
    # Together with session_by_market these give the full-runtime "Session" P&L
    # that spans uninterrupted across day boundaries without double-counting.
    session_pre_midnight_fills: int = 0
    session_pre_midnight_cash: float = 0.0
    session_pre_midnight_realized: float = 0.0

    # Persistence: pre-session snapshot of today's fill ledger (set by MakerStateLoader)
    today_stats: dict = field(default_factory=dict)

    # Persistence: all-time stats for days before today (set by MakerStateLoader)
    lifetime_stats: dict = field(default_factory=dict)

    # Lock for concurrent actor access
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def get_inventory(self, token_id: str) -> float:
        return self.inventory.get(token_id, 0.0)

    def update_inventory(self, token_id: str, side: str, size: float) -> None:
        current = self.inventory.get(token_id, 0.0)
        if side == "BUY":
            current += size
        else:
            current -= size
        self.inventory[token_id] = current
        if current != 0.0:
            self.inventory_entry_time.setdefault(token_id, time.time())
        else:
            self.inventory_entry_time.pop(token_id, None)

    @property
    def total_abs_inventory(self) -> float:
        return sum(abs(v) for v in self.inventory.values())

    def skew_factor(self, token_id: str, category: str = "") -> float:
        """Normalized skew: [-1.0, +1.0]. Positive = holding YES.

        Uses per-category cap when category is provided; falls back to
        max_inventory_per_market for unknown or unspecified categories.
        """
        inv = self.get_inventory(token_id)
        cap = (self.max_inventory_for_category(category)
               if category else self.max_inventory_per_market)
        if cap == 0:
            return 0.0
        return max(-1.0, min(1.0, inv / cap))

    def max_inventory_for_category(self, category: str) -> float:
        """Per-category cap, falling back to max_inventory_per_market."""
        return self.category_inventory_caps.get(
            category.lower() if category else "", self.max_inventory_per_market
        )

    def in_cooldown(self, token_id: str) -> bool:
        """True if market is in cooldown (circuit breaker fired recently)."""
        resume_at = self.cooldowns.get(token_id, 0.0)
        return time.time() < resume_at

    def global_in_cooldown(self) -> bool:
        """True if total inventory cap cooldown is active — no quoting on any market."""
        return time.time() < self.global_cooldown_until

    def record_fill(self, token_id: str, side: str, price: float, size: float, filled_at: float,
                    question: str = "", end_date_iso: str = "", *, _track_session: bool = True) -> None:
        """Record fill in history, update cash P&L, and compute realized P&L via FIFO lot matching.

        _track_session=False during startup replay — suppresses session_by_market updates since
        those fills are already counted in today_stats (read from the ledger at startup).
        """
        _realized_before = self.realized_pnl

        # cash_pnl tracks raw cash flows only — do NOT use this for decision-making.
        # Use mtm_pnl(markets) for a number that accounts for open positions.
        cash_flow = (price * size) if side == "SELL" else -(price * size)
        self.cash_pnl += cash_flow
        self.total_fills += 1

        # FIFO lot matching — book realized P&L when this fill closes existing opposite lots.
        # Formula: realized = (sell_price - buy_price) * matched_size
        # Works for both long round-trips (BUY→SELL) and short round-trips (SELL→BUY).
        lots = self._open_lots.setdefault(token_id, [])
        opposite = "SELL" if side == "BUY" else "BUY"
        remaining = size
        new_lots = []
        for lot_side, lot_price, lot_size in lots:
            if lot_side == opposite and remaining > 0:
                matched = min(lot_size, remaining)
                sell_p = price if side == "SELL" else lot_price
                buy_p  = price if side == "BUY"  else lot_price
                self.realized_pnl += (sell_p - buy_p) * matched
                remaining -= matched
                if lot_size > matched:
                    new_lots.append((lot_side, lot_price, lot_size - matched))
            else:
                new_lots.append((lot_side, lot_price, lot_size))
        if remaining > 0:
            new_lots.append((side, price, remaining))
        self._open_lots[token_id] = new_lots
        entry = {
            "token_id": token_id[:16],
            "side": side,
            "price": round(price, 4),
            "size": round(size, 2),
            "filled_at": round(filled_at, 1),
            "question": question,
            "end_date_iso": end_date_iso,
        }
        self.fill_history.insert(0, entry)   # newest first
        if len(self.fill_history) > 100:
            self.fill_history.pop()

        if _track_session:
            mkt = self.session_by_market.setdefault(token_id, {
                "fills": 0, "cash_pnl": 0.0, "realized_pnl": 0.0, "question": question
            })
            mkt["fills"] += 1
            if question:
                mkt["question"] = question
            mkt["cash_pnl"] += cash_flow
            mkt["realized_pnl"] += self.realized_pnl - _realized_before

    def mtm_pnl(self, markets: dict) -> float:
        """
        Mark-to-market P&L: cash_pnl + current value of all open positions.

        cash_pnl alone is misleading — selling YES at 0.80 looks like +$8 profit
        but you're short 10 units now worth $8 at current price, so net effect
        on your real wealth is ~$0 until the position closes or resolves.

        mtm_pnl = cash_flow_pnl + sum(net_position[t] * current_mid[t])

        This is 0 on first fill and only moves when you capture spread or the
        market moves in your favour after you've taken a position.
        """
        position_value = 0.0
        for token_id, net_units in self.inventory.items():
            cs = markets.get(token_id)
            if cs is not None:
                position_value += net_units * cs.mid
        return self.cash_pnl + position_value

    def reset_daily(self) -> None:
        self.cash_pnl = 0.0
        self.realized_pnl = 0.0
        self._open_lots.clear()
