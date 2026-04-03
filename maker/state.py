"""Shared mutable state for the maker bot."""

import asyncio
import time
from dataclasses import dataclass, field
from maker.types import QuoteIntent


@dataclass
class MakerState:
    """Thread-safe state shared across all maker actors."""

    max_inventory_per_market: float = 50.0
    max_total_inventory: float = 200.0

    # Per-market net position: positive = holding YES, negative = holding NO
    inventory: dict[str, float] = field(default_factory=dict)

    # Per-market live order IDs: token_id → {"bid_order_id": str, "ask_order_id": str, "bid_price": float, ...}
    live_orders: dict[str, list[dict]] = field(default_factory=dict)

    # Per-market last emitted quote (for stale detection)
    last_quotes: dict[str, QuoteIntent] = field(default_factory=dict)

    # Per-market cooldown: token_id → resume_at (unix timestamp)
    cooldowns: dict[str, float] = field(default_factory=dict)

    # Daily realized P&L
    daily_pnl: float = 0.0

    # Fill history for dashboard (capped at 100)
    fill_history: list = field(default_factory=list)

    # Fill timestamps for rapid-fill detection: token_id → {side → timestamp}
    last_fill_times: dict[str, dict[str, float]] = field(default_factory=dict)

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

    @property
    def total_abs_inventory(self) -> float:
        return sum(abs(v) for v in self.inventory.values())

    def skew_factor(self, token_id: str) -> float:
        """Normalized skew: [-1.0, +1.0]. Positive = holding YES."""
        inv = self.get_inventory(token_id)
        if self.max_inventory_per_market == 0:
            return 0.0
        raw = inv / self.max_inventory_per_market
        return max(-1.0, min(1.0, raw))

    def in_cooldown(self, token_id: str) -> bool:
        """True if market is in cooldown (circuit breaker fired recently)."""
        resume_at = self.cooldowns.get(token_id, 0.0)
        return time.time() < resume_at

    def record_fill(self, token_id: str, side: str, price: float, size: float, filled_at: float) -> None:
        """Record fill in history and update realized P&L (mark-to-cost basis)."""
        # P&L contribution: SELL fills at price p realize p*size, BUY fills cost p*size
        # Simple signed-PnL: +price*size for SELL, -price*size for BUY
        if side == "SELL":
            self.daily_pnl += price * size
        else:
            self.daily_pnl -= price * size
        entry = {
            "token_id": token_id[:16],
            "side": side,
            "price": round(price, 4),
            "size": round(size, 2),
            "filled_at": round(filled_at, 1),
        }
        self.fill_history.insert(0, entry)   # newest first
        if len(self.fill_history) > 100:
            self.fill_history.pop()

    def reset_daily(self) -> None:
        self.daily_pnl = 0.0
