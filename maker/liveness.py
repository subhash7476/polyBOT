"""Liveness monitoring — verifies the bot's claimed live orders are actually
live on the CLOB and surfaces silent failures (heartbeat staleness, orders
silently cancelled, repeated auth errors) via a pluggable alert sink.

Channel-agnostic by design: today we write to logs/alerts.log + stdout. When
Telegram BotFather is unblocked, add a TelegramSink and append it to the
dispatcher in runner.py — no other changes needed.
"""

import asyncio
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from utils.logger import get_logger

log = get_logger(__name__)


# --- Alert sinks -------------------------------------------------------------


class Sink(ABC):
    @abstractmethod
    def push(self, severity: str, key: str, message: str,
             payload: dict | None = None) -> None: ...


class StdoutSink(Sink):
    def push(self, severity, key, message, payload=None):
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts}] ALERT[{severity}] {key}: {message}", flush=True)


class FileSink(Sink):
    """Appends one line per alert to logs/alerts.log."""

    def __init__(self, path: str = "logs/alerts.log"):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def push(self, severity, key, message, payload=None):
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        line = f"{ts} | {severity:8s} | {key:30s} | {message}"
        if payload:
            line += f" | {json.dumps(payload, default=str, separators=(',', ':'))}"
        with self._path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


# --- Shared state ------------------------------------------------------------


@dataclass
class LivenessState:
    """Fields written by various actors, read by LivenessMonitor + /health."""
    last_heartbeat_ok_ts: float = 0.0          # set by _heartbeat_loop on success
    last_heartbeat_attempt_ts: float = 0.0     # set every tick regardless of outcome
    consecutive_heartbeat_failures: int = 0
    last_order_place_ts: float = 0.0           # set by OrderManager on ORDER OK
    last_order_fail_ts: float = 0.0
    last_order_fail_reason: str = ""
    recent_auth_failures: list[float] = field(default_factory=list)  # 401/403 timestamps

    def snapshot(self) -> dict:
        now = time.time()
        return {
            "now": now,
            "heartbeat_age_s": (now - self.last_heartbeat_ok_ts) if self.last_heartbeat_ok_ts else None,
            "heartbeat_consecutive_failures": self.consecutive_heartbeat_failures,
            "last_order_place_age_s": (now - self.last_order_place_ts) if self.last_order_place_ts else None,
            "last_order_fail_age_s": (now - self.last_order_fail_ts) if self.last_order_fail_ts else None,
            "last_order_fail_reason": self.last_order_fail_reason,
            "auth_failures_last_5min": sum(1 for t in self.recent_auth_failures if now - t < 300),
        }


# --- Alert dispatcher --------------------------------------------------------


class AlertDispatcher:
    """Routes alerts to all configured sinks with per-key 5-minute dedup."""
    DEDUP_WINDOW = 300.0

    def __init__(self, sinks: list[Sink]):
        self._sinks = sinks
        self._last_sent: dict[str, float] = {}

    def push(self, severity: str, key: str, message: str,
             payload: dict | None = None) -> None:
        now = time.time()
        last = self._last_sent.get(key, 0.0)
        if now - last < self.DEDUP_WINDOW:
            return
        self._last_sent[key] = now
        for sink in self._sinks:
            try:
                sink.push(severity, key, message, payload)
            except Exception as exc:
                log.exception(f"alert sink {type(sink).__name__} failed: {exc}")


# --- Liveness monitor coroutine ---------------------------------------------


class LivenessMonitor:
    """Background actor: every `interval` seconds verifies bot-tracked orders
    against CLOB and checks heartbeat freshness.
    """
    HEARTBEAT_STALE_SECONDS = 10.0  # Polymarket order TTL ~10s; warn at threshold

    def __init__(self, clob, maker_state, liveness_state: LivenessState,
                 dispatcher: AlertDispatcher, interval: float = 30.0):
        self._clob = clob
        self._maker = maker_state
        self._ls = liveness_state
        self._d = dispatcher
        self._interval = interval

    async def run(self) -> None:
        log.info(f"liveness monitor started (interval={self._interval:.0f}s)")
        while True:
            await asyncio.sleep(self._interval)
            try:
                self._check_heartbeat()
                await self._check_orders()
            except Exception as exc:
                log.exception(f"liveness monitor tick failed: {exc}")

    def _check_heartbeat(self) -> None:
        if self._ls.last_heartbeat_ok_ts == 0.0:
            return
        age = time.time() - self._ls.last_heartbeat_ok_ts
        if age > self.HEARTBEAT_STALE_SECONDS:
            self._d.push(
                "CRITICAL", "heartbeat_stale",
                f"heartbeat last OK {age:.0f}s ago (Polymarket TTL ~10s) — "
                f"orders may be auto-cancelled",
                {"age_seconds": round(age, 1),
                 "consecutive_failures": self._ls.consecutive_heartbeat_failures},
            )

    async def _check_orders(self) -> None:
        if self._clob is None:
            return
        async with self._maker._lock:
            claimed = {
                token_id: [o.get("id") or o.get("order_id") for o in orders]
                for token_id, orders in self._maker.live_orders.items()
            }
        if not claimed:
            return
        loop = asyncio.get_running_loop()
        for token_id, order_ids in claimed.items():
            for oid in order_ids:
                if not oid:
                    continue
                try:
                    order = await loop.run_in_executor(None, self._clob.get_order, oid)
                except Exception as exc:
                    self._d.push(
                        "WARNING", f"get_order_err_{oid[:8]}",
                        f"failed to query order {oid[:12]}: {exc}",
                    )
                    continue
                status = (order or {}).get("status", "").upper()
                if status and status not in ("LIVE", "MATCHED", "PARTIALLY_MATCHED"):
                    self._d.push(
                        "CRITICAL", f"order_dead_{oid[:8]}",
                        f"order {oid[:12]} status={status} on CLOB but bot still tracks as live",
                        {"token_id": token_id, "order_id": oid, "clob_status": status},
                    )
