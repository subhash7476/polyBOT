"""Asyncio loop that snapshots MakerState -> MakerDashboardState every 2s."""
import asyncio
import time

from market.state import AppState
from maker.state import MakerState
from maker.dashboard_state import MakerDashboardState

_start_time = time.time()


async def maker_dashboard_loop(
    app_state: AppState,
    maker_state: MakerState,
    dash: MakerDashboardState,
    interval: float = 2.0,
) -> None:
    while True:
        try:
            async with app_state._lock:
                markets_snapshot = dict(app_state.markets)
                feed_stamps = dict(app_state.feeds.last_feed_update)

            async with maker_state._lock:
                inventory = dict(maker_state.inventory)
                live_orders = dict(maker_state.live_orders)
                cash_pnl = maker_state.cash_pnl
                fill_history = list(maker_state.fill_history[:20])  # last 20
                cooldowns = [
                    tid for tid, exp in maker_state.cooldowns.items()
                    if exp > time.time()
                ]

            total_abs = sum(abs(v) for v in inventory.values())

            # Mark-to-market P&L: cash flows + current value of open positions
            position_value = sum(
                net * (markets_snapshot[tid].mid if tid in markets_snapshot else 0.0)
                for tid, net in inventory.items()
            )
            mtm_pnl = cash_pnl + position_value

            # Build active markets list — live_orders[token_id] is list[dict] (ladder levels)
            active_markets = []
            for token_id, levels in live_orders.items():
                cs = markets_snapshot.get(token_id)
                question = cs.question[:60] if cs else token_id[:16]
                # Use center level for display (index 1 for 3-level ladder)
                center = levels[len(levels) // 2] if levels else {}
                bid_p = center.get("bid_price", 0.0)
                ask_p = center.get("ask_price", 0.0)
                spread = round(ask_p - bid_p, 4)
                inv = inventory.get(token_id, 0.0)
                in_cd = token_id in cooldowns
                active_markets.append({
                    "token_id": token_id[:16],
                    "question": question,
                    "bid": bid_p,
                    "ask": ask_p,
                    "spread": spread,
                    "n_levels": len(levels),
                    "inventory": round(inv, 2),
                    "cooldown": in_cd,
                })

            # Feed ages
            now = time.time()

            def _age(name):
                ts = feed_stamps.get(name, 0)
                if ts == 0:
                    return "?"
                s = int(now - ts)
                return f"{s}s" if s < 60 else f"{s // 60}m{s % 60}s"

            dash.update({
                "uptime_seconds": round(now - _start_time, 0),
                "inventory": {k: round(v, 2) for k, v in inventory.items()},
                "live_orders": live_orders,
                "cooldowns": cooldowns,
                "daily_pnl": round(mtm_pnl, 4),        # MTM — the real number
                "cash_pnl": round(cash_pnl, 4),         # cash flows only (misleading alone)
                "total_abs_inventory": round(total_abs, 2),
                "active_markets": active_markets,
                "fills": fill_history,
                "n_active_markets": len(live_orders),
                "clob_age": _age("clob"),
                "micro_age": _age("microstructure"),
            })
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(f"dashboard_loop error: {exc}")

        await asyncio.sleep(interval)
