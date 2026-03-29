"""
trading/risk.py — RiskManager v2

v2.1 Fix 3: _contract_group_key() replaces the flat ASSET_GROUPS dict.
BTC-above and BTC-below are separate exposure buckets — two contracts that are
both BTC long (above $85k and above $90k) share the same "btc_above" group,
preventing silent 2× BTC exposure.
"""

import asyncio
import time
from market.state import FeedState
from engine.contract_parser import ParsedContract
from trading.positions import (
    CLOSED_PAPER,
    OPEN,
    REDEEMED,
    RESOLVED_PENDING_REDEEM,
    PositionLedger,
    TrackedPosition,
)
from utils.logger import get_logger
import config

log = get_logger(__name__)


class RiskManager:
    def __init__(self, bankroll: float, ledger: PositionLedger | None = None):
        self.bankroll = bankroll
        self.max_daily_loss = bankroll * config.MAX_DAILY_LOSS_PCT
        self.max_position = bankroll * config.MAX_POSITION_PCT
        self.max_group_exposure = bankroll * config.MAX_GROUP_EXPOSURE_PCT
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        self.ledger = ledger or PositionLedger(config.TRACKED_POSITIONS_FILE)
        self.open_positions, self.pending_redemptions = self.ledger.load_active()
        self.closed_positions: dict[str, TrackedPosition] = {}
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
        if parsed.category == "weather":
            return f"weather_{parsed.asset}"
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
        self,
        token_id: str,
        parsed: ParsedContract,
        size: float,
        price: float,
        side: str = "BUY_YES",
        *,
        no_token_id: str = "",
        condition_id: str = "",
        question: str = "",
        category: str | None = None,
        market_price_at_open: float | None = None,
        strategy_type: str = "directional",
        opened_at: float | None = None,
    ):
        async with self._lock:
            position = TrackedPosition(
                token_id=token_id,
                no_token_id=no_token_id,
                condition_id=condition_id,
                question=question or parsed.question,
                category=category or parsed.category,
                group_key=self._contract_group_key(parsed),
                side=side,
                size_usdc=size,
                entry_price=price,
                market_price_at_open=market_price_at_open if market_price_at_open is not None else price,
                strategy_type=strategy_type,
                status=OPEN,
                opened_at=opened_at or time.time(),
            )
            self.open_positions[token_id] = position
            self.ledger.append(position)

    async def close_position(self, token_id: str, exit_price: float):
        async with self._lock:
            pos = self.open_positions.pop(token_id, None)
            if pos:
                pnl = (exit_price - pos.entry_price) * pos.shares
                self.daily_pnl += pnl
                if pnl < 0:
                    self.consecutive_losses += 1
                else:
                    self.consecutive_losses = 0
                log.info(f"closed {token_id[:8]} pnl=${pnl:.2f} daily_pnl=${self.daily_pnl:.2f}")

    async def resolve_position(
        self,
        token_id: str,
        resolved_yes: bool,
        *,
        paper: bool,
        resolved_at: float | None = None,
    ) -> TrackedPosition | None:
        async with self._lock:
            pos = self.open_positions.pop(token_id, None)
            if not pos:
                return None
            payout = 1.0 if ((pos.side == "BUY_YES" and resolved_yes) or (pos.side == "BUY_NO" and not resolved_yes)) else 0.0
            pnl = (payout - pos.entry_price) * pos.shares
            self.daily_pnl += pnl
            if pnl < 0:
                self.consecutive_losses += 1
            else:
                self.consecutive_losses = 0

            pos.resolved_yes = resolved_yes
            pos.resolved_at = resolved_at or time.time()
            pos.status = CLOSED_PAPER if paper else RESOLVED_PENDING_REDEEM

            if paper:
                self.closed_positions[token_id] = pos
            else:
                self.pending_redemptions[token_id] = pos

            self.ledger.append(pos)
            log.info(
                f"resolved {token_id[:8]} side={pos.side} outcome={'YES' if resolved_yes else 'NO'} "
                f"status={pos.status} pnl=${pnl:.2f} daily_pnl=${self.daily_pnl:.2f}"
            )
            return pos

    async def mark_redeemed(
        self,
        token_id: str,
        *,
        redeemed_at: float | None = None,
    ) -> TrackedPosition | None:
        async with self._lock:
            pos = self.pending_redemptions.pop(token_id, None)
            if not pos:
                return None
            pos.status = REDEEMED
            pos.redeemed_at = redeemed_at or time.time()
            self.closed_positions[token_id] = pos
            self.ledger.append(pos)
            return pos

    async def update_wallet_metadata(
        self,
        token_id: str,
        *,
        condition_id: str = "",
    ) -> TrackedPosition | None:
        async with self._lock:
            pos = self.open_positions.get(token_id) or self.pending_redemptions.get(token_id)
            if not pos:
                return None
            changed = False
            if condition_id and pos.condition_id != condition_id:
                pos.condition_id = condition_id
                changed = True
            if changed:
                self.ledger.append(pos)
            return pos

    def expire_paper_positions(self, ttl_hours: float) -> int:
        """
        Remove paper positions older than ttl_hours.
        Called each scan cycle in paper mode so the bot keeps exploring.
        Returns number of positions expired.
        """
        now = time.time()
        cutoff = now - ttl_hours * 3600
        expired = [
            tid for tid, pos in self.open_positions.items()
            if pos.opened_at < cutoff
        ]
        for tid in expired:
            pos = self.open_positions.pop(tid)
            held_h = (now - pos.opened_at) / 3600
            pos.status = CLOSED_PAPER
            self.ledger.append(pos)
            log.info(f"paper position expired: {tid[:8]} (held {held_h:.1f}h)")
        return len(expired)

    def reset_daily(self):
        self.daily_pnl = 0.0
