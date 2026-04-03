"""Thread-safe snapshot store for the maker bot dashboard."""
import json
import threading
import time
from collections import deque


class MakerDashboardState:
    def __init__(self):
        self._lock = threading.Lock()
        self.uptime_seconds: float = 0.0
        self.paper: bool = True
        # Active quoted markets: list of dicts
        self.active_markets: list = []
        # Live orders: dict token_id -> {bid_price, ask_price, bid_size, ask_size}
        self.live_orders: dict = {}
        # Inventory: dict token_id -> float
        self.inventory: dict = {}
        # Cooldowns: list of token_id strings currently cooled down
        self.cooldowns: list = []
        # Mark-to-market P&L (cash flows + open position value at current mid)
        self.daily_pnl: float = 0.0
        # Raw cash-flow P&L (fills only, ignores open positions — can be misleading)
        self.cash_pnl: float = 0.0
        # Total absolute inventory
        self.total_abs_inventory: float = 0.0
        # Fill history: deque of fill dicts
        self.fills: deque = deque(maxlen=50)
        # Circuit breaker fires today
        self.cb_fires: int = 0
        # MarketSelector stats
        self.n_active_markets: int = 0
        self.last_refresh: float = 0.0
        # Feed ages
        self.clob_age: str = "?"
        self.micro_age: str = "?"

    def update(self, snapshot: dict) -> None:
        with self._lock:
            for key, val in snapshot.items():
                if key == "fills_append":
                    self.fills.appendleft(val)  # newest first
                elif hasattr(self, key):
                    setattr(self, key, val)

    def to_json(self) -> str:
        with self._lock:
            return json.dumps({
                "uptime_seconds": self.uptime_seconds,
                "paper": self.paper,
                "active_markets": self.active_markets,
                "live_orders": self.live_orders,
                "inventory": self.inventory,
                "cooldowns": self.cooldowns,
                "daily_pnl": self.daily_pnl,
                "cash_pnl": self.cash_pnl,
                "total_abs_inventory": self.total_abs_inventory,
                "fills": list(self.fills),
                "cb_fires": self.cb_fires,
                "n_active_markets": self.n_active_markets,
                "last_refresh": self.last_refresh,
                "clob_age": self.clob_age,
                "micro_age": self.micro_age,
            })
