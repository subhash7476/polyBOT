"""
trading/risk.py — RiskManager v2

v2.1 Fix 3: _contract_group_key() replaces the flat ASSET_GROUPS dict.
BTC-above and BTC-below are separate exposure buckets — two contracts that are
both BTC long (above $85k and above $90k) share the same "btc_above" group,
preventing silent 2× BTC exposure.
"""

import asyncio
from dataclasses import dataclass
from market.state import FeedState
from engine.contract_parser import ParsedContract
from utils.logger import get_logger
import config

log = get_logger(__name__)


@dataclass
class Position:
    token_id: str
    group_key: str
    size_usdc: float
    entry_price: float


class RiskManager:
    def __init__(self, bankroll: float):
        self.bankroll = bankroll
        self.max_daily_loss = bankroll * config.MAX_DAILY_LOSS_PCT
        self.max_position = bankroll * config.MAX_POSITION_PCT
        self.max_group_exposure = bankroll * config.MAX_GROUP_EXPOSURE_PCT
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        self.open_positions: dict[str, Position] = {}
        self._lock = asyncio.Lock()

    _CRYPTO_ASSETS = {"BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "AVAX"}

    @staticmethod
    def _contract_group_key(parsed: ParsedContract) -> str:
        """
        Direction-bucketed group key. All contracts for the same asset+direction
        share an exposure bucket: "sol_above", "btc_below", etc.
        """
        if parsed.asset in RiskManager._CRYPTO_ASSETS:
            return f"{parsed.asset.lower()}_{parsed.direction or 'above'}"
        if parsed.category == "rates":
            return "macro_rates"
        if parsed.category == "macro":
            return "macro_econ"
        return "other"

    @property
    def ev_multiplier(self) -> float:
        """Raise EV threshold after consecutive losses."""
        if self.consecutive_losses >= 3:
            return 2.0
        if self.consecutive_losses >= 1:
            return 1.5
        return 1.0

    async def can_trade(
        self,
        token_id: str,
        parsed: ParsedContract,
        position_size: float,
        feeds: FeedState,
    ) -> tuple[bool, str]:
        async with self._lock:
            # 0. Already in this position
            if token_id in self.open_positions:
                return False, "position already open"

            # 1. Daily loss hard stop
            if self.daily_pnl <= -self.max_daily_loss:
                return False, "daily loss limit hit"

            # 2. Max open positions
            if len(self.open_positions) >= config.MAX_OPEN_POSITIONS:
                return False, "max open positions reached"

            # 3. Per-position size cap
            if position_size > self.max_position:
                return False, f"position ${position_size:.0f} > max ${self.max_position:.0f}"

            # 4. Direction-bucketed group exposure limit
            group_key = self._contract_group_key(parsed)
            group_exposure = sum(
                p.size_usdc for p in self.open_positions.values()
                if p.group_key == group_key
            )
            if group_exposure + position_size > self.max_group_exposure:
                return False, f"group '{group_key}' exposure would exceed {config.MAX_GROUP_EXPOSURE_PCT:.0%}"

            # 5. High-vol regime: halve max position if DVOL > 80
            dvol = feeds.btc_dvol or 60
            if dvol > 80 and position_size > self.max_position * 0.5:
                return False, f"high DVOL regime ({dvol:.0f}): position too large"

            return True, "ok"

    async def open_position(
        self, token_id: str, parsed: ParsedContract, size: float, price: float
    ):
        async with self._lock:
            self.open_positions[token_id] = Position(
                token_id=token_id,
                group_key=self._contract_group_key(parsed),
                size_usdc=size,
                entry_price=price,
            )

    async def close_position(self, token_id: str, exit_price: float):
        async with self._lock:
            pos = self.open_positions.pop(token_id, None)
            if pos:
                pnl = (exit_price - pos.entry_price) * pos.size_usdc
                self.daily_pnl += pnl
                if pnl < 0:
                    self.consecutive_losses += 1
                else:
                    self.consecutive_losses = 0
                log.info(f"closed {token_id[:8]} pnl=${pnl:.2f} daily_pnl=${self.daily_pnl:.2f}")

    def reset_daily(self):
        self.daily_pnl = 0.0
