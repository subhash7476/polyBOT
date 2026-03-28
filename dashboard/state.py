"""Thread-safe snapshot store for the real-time dashboard.

Written to by dashboard_loop() (asyncio coroutine, inside event loop).
Read by the Flask daemon thread via to_json().
"""
import json
import threading
import time
from collections import deque


class DashboardState:
    def __init__(self):
        self._lock = threading.Lock()

        # Bot meta
        self.bot_status: str = "running"
        self.uptime_seconds: float = 0.0

        # Per-asset feed data
        self.spot_prices: dict = {}
        self.dvol: dict = {}
        self.funding_rates: dict = {}
        self.vol_skew: dict = {}

        # Macro
        self.dxy: float | None = None
        self.dxy_confidence: float | None = None
        self.yield_10y: float | None = None
        self.fed_may_cut_prob: float | None = None
        self.fed_expected_cuts: float | None = None
        self.sofr: float | None = None
        self.cpi: float | None = None
        self.unrate: float | None = None

        # Trading activity
        self.positions: list = []
        self.position_counts: dict = {
            "open": 0,
            "resolved_pending_redeem": 0,
            "closed": 0,
        }
        self.realized_pnl: float = 0.0
        self.consecutive_losses: int = 0
        self.active_markets: deque = deque(maxlen=50)

        # Lifecycle panel fields
        self.open_count: int = 0
        self.open_exposure_usdc: float = 0.0
        self.pending_redeem_count: int = 0
        self.redeemed_count: int = 0
        self.realized_pnl_total: float = 0.0
        self.realized_pnl_today: float = 0.0
        self.lifetime_wins: int = 0
        self.lifetime_losses: int = 0
        self.scan_stats: dict = {
            "n_total": 0, "n_parseable": 0, "n_signal": 0,
            "n_liquidity": 0, "n_ev": 0, "n_traded": 0,
            "lifetime_trades": 0,
        }

        # Per-source update timestamps (epoch float)
        self.feed_updated_at: dict = {}

    def update(self, snapshot: dict) -> None:
        with self._lock:
            for key, val in snapshot.items():
                if key == "active_markets_append":
                    self.active_markets.append(val)
                elif key == "feed_updated_at":
                    self.feed_updated_at.update(val)
                elif hasattr(self, key):
                    setattr(self, key, val)
                # Unknown keys silently ignored

    def to_json(self) -> str:
        with self._lock:
            now = time.time()

            feed_ages = {}
            for feed, ts in self.feed_updated_at.items():
                age = now - ts
                if age < 60:
                    feed_ages[feed] = f"{int(age)}s ago"
                elif age < 3600:
                    feed_ages[feed] = f"{int(age / 60)}m ago"
                else:
                    feed_ages[feed] = f"{int(age / 3600)}h ago"

            return json.dumps({
                "bot_status": self.bot_status,
                "uptime_seconds": self.uptime_seconds,
                "spot_prices": self.spot_prices,
                "dvol": self.dvol,
                "funding_rates": self.funding_rates,
                "vol_skew": self.vol_skew,
                "dxy": self.dxy,
                "dxy_confidence": self.dxy_confidence,
                "yield_10y": self.yield_10y,
                "fed_may_cut_prob": self.fed_may_cut_prob,
                "fed_expected_cuts": self.fed_expected_cuts,
                "sofr": self.sofr,
                "cpi": self.cpi,
                "unrate": self.unrate,
                "positions": self.positions,
                "position_counts": self.position_counts,
                "realized_pnl": self.realized_pnl,
                "consecutive_losses": self.consecutive_losses,
                "active_markets": list(self.active_markets),
                "scan_stats": self.scan_stats,
                "lifetime_trades": self.scan_stats.get("lifetime_trades", 0),
                "feed_ages": feed_ages,
                "open_count": self.open_count,
                "open_exposure_usdc": self.open_exposure_usdc,
                "pending_redeem_count": self.pending_redeem_count,
                "redeemed_count": self.redeemed_count,
                "realized_pnl_total": self.realized_pnl_total,
                "realized_pnl_today": self.realized_pnl_today,
                "lifetime_wins": self.lifetime_wins,
                "lifetime_losses": self.lifetime_losses,
            })
