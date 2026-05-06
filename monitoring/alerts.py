"""
monitoring/alerts.py

Telegram alert system for key bot events.

Sends notifications for:
- Trade executed (paper or live)
- Arb opportunity detected
- Daily P&L summary
- Feed disconnection
- Risk limit approaching (>80% of daily loss limit)
- Flatline pattern detected on high-volume market

Configure via .env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
Uses raw HTTP (same approach as telegram_bot.py — no python-telegram-bot dependency).
"""
import asyncio
from enum import Enum
from typing import Optional

import httpx
from utils.logger import get_logger

log = get_logger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


class AlertType(Enum):
    TRADE_EXECUTED = "trade"
    ARB_DETECTED = "arb"
    DAILY_SUMMARY = "summary"
    FEED_DISCONNECTED = "feed_down"
    RISK_LIMIT_APPROACHING = "risk"
    FLATLINE_DETECTED = "flatline"


class AlertManager:
    """
    Sends Telegram alerts. Silently disabled if bot_token or chat_id is empty.
    All send() calls are fire-and-forget (logged on failure, never raise).
    """

    def __init__(self, bot_token: str = "", chat_id: str = ""):
        from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
        self._token = bot_token or TELEGRAM_BOT_TOKEN
        self._chat_id = chat_id or TELEGRAM_CHAT_ID
        self.enabled = bool(self._token and self._chat_id)
        if not self.enabled:
            log.debug("AlertManager: Telegram not configured — alerts disabled")

    @property
    def _url(self) -> str:
        return _TELEGRAM_API.format(token=self._token)

    async def send(self, alert_type: AlertType, message: str) -> bool:
        """Send alert. Returns True on success, False on failure/disabled."""
        if not self.enabled:
            return False
        prefix = {
            AlertType.TRADE_EXECUTED:         "[TRADE]",
            AlertType.ARB_DETECTED:           "[ARB]",
            AlertType.DAILY_SUMMARY:          "[SUMMARY]",
            AlertType.FEED_DISCONNECTED:      "[FEED DOWN]",
            AlertType.RISK_LIMIT_APPROACHING: "[RISK ALERT]",
            AlertType.FLATLINE_DETECTED:      "[FLATLINE]",
        }.get(alert_type, "[ALERT]")
        full_msg = f"{prefix} {message}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(self._url, json={
                    "chat_id": self._chat_id,
                    "text": full_msg,
                    "parse_mode": "HTML",
                })
                if resp.status_code != 200:
                    log.warning(f"Telegram alert failed: {resp.status_code} {resp.text[:100]}")
                    return False
            return True
        except Exception as exc:
            log.warning(f"Telegram alert error: {exc}")
            return False

    def format_trade(
        self,
        side: str,
        question: str,
        size: float,
        model_prob: float,
        market_mid: float,
        ev: float,
    ) -> str:
        from config import PAPER
        paper_tag = "[PAPER] " if PAPER else ""
        return (
            f"{paper_tag}{side} ${size:.2f}\n"
            f"Q: {question[:60]}\n"
            f"Model: {model_prob:.2%} | Market: {market_mid:.2%} | EV: {ev:.2%}"
        )

    def format_arb(self, description: str, spread: float) -> str:
        return f"Arb detected — spread={spread:.3f}\n{description}"

    def format_daily_summary(
        self,
        session_pnl: float,
        trades_today: int,
        open_positions: int,
    ) -> str:
        pnl_sign = "+" if session_pnl >= 0 else ""
        return (
            f"Daily P&L: {pnl_sign}${session_pnl:.2f}\n"
            f"Trades: {trades_today} | Open positions: {open_positions}"
        )

    def format_feed_down(self, feed_name: str, seconds_stale: float) -> str:
        return f"Feed '{feed_name}' has been stale for {seconds_stale:.0f}s — check connection"

    def format_risk_alert(self, daily_loss: float, daily_limit: float) -> str:
        pct = daily_loss / daily_limit * 100 if daily_limit else 0
        return f"Risk limit at {pct:.0f}%: -${daily_loss:.2f} / limit -${daily_limit:.2f}"

    def format_flatline(self, question: str, hours_left: float, leading_price: float) -> str:
        return (
            f"Flatline pattern on high-volume market:\n"
            f"Q: {question[:60]}\n"
            f"Leading price: {leading_price:.2%} | {hours_left:.1f}h to resolution"
        )


# Singleton for use from main.py
_alert_manager: Optional[AlertManager] = None


def get_alert_manager() -> AlertManager:
    global _alert_manager
    if _alert_manager is None:
        _alert_manager = AlertManager()
    return _alert_manager
