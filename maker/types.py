"""Shared dataclasses for maker actor communication."""

from dataclasses import dataclass


@dataclass(frozen=True)
class QuoteIntent:
    """Emitted by QuoteEngine → consumed by OrderManager."""
    token_id: str
    bid_price: float
    ask_price: float
    bid_size: float
    ask_size: float
    reason: str  # "new_market" | "reprice" | "inventory_skew"

    @property
    def spread(self) -> float:
        return round(self.ask_price - self.bid_price, 10)


@dataclass(frozen=True)
class Fill:
    """Emitted by FillPoller → consumed by InventoryManager."""
    token_id: str
    side: str  # "BUY" | "SELL"
    price: float
    size: float
    order_id: str
    filled_at: float  # unix timestamp

    @property
    def notional(self) -> float:
        return self.size


@dataclass(frozen=True)
class SkewUpdate:
    """Emitted by InventoryManager → consumed by QuoteEngine."""
    token_id: str
    skew_factor: float  # [-1.0, +1.0]; positive = holding YES


@dataclass(frozen=True)
class CancelAll:
    """Emitted by CircuitBreaker → consumed by OrderManager."""
    token_id: str  # specific market or "*" for all

    @property
    def is_global(self) -> bool:
        return self.token_id == "*"
