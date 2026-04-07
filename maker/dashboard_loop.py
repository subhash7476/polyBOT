"""Asyncio loop that snapshots MakerState -> MakerDashboardState every 2s."""
import asyncio
import time
from typing import TYPE_CHECKING

from market.state import AppState
from maker.state import MakerState
from maker.dashboard_state import MakerDashboardState
from engine.falcon_signals import (
    get_elite_wallets,
    get_whale_controlled_markets,
    get_spiking_markets,
    compute_adverse_selection_penalty,
    _WALLET_STALE_SECONDS,
    _INSIGHTS_STALE_SECONDS,
    _HIGH_TRUST_THRESHOLD,
)

if TYPE_CHECKING:
    from maker.markout_tracker import MarkoutTracker

_start_time = time.time()


async def maker_dashboard_loop(
    app_state: AppState,
    maker_state: MakerState,
    dash: MakerDashboardState,
    interval: float = 2.0,
    markout_tracker: "MarkoutTracker | None" = None,
) -> None:
    _uptime_samples = 0
    _quoted_samples = 0

    while True:
        try:
            async with app_state._lock:
                markets_snapshot = dict(app_state.markets)
                feed_stamps = dict(app_state.feeds.last_feed_update)
                feeds = app_state.feeds  # reference for Falcon reads

            async with maker_state._lock:
                inventory = dict(maker_state.inventory)
                live_orders = dict(maker_state.live_orders)
                cash_pnl = maker_state.cash_pnl
                fill_history = list(maker_state.fill_history[:20])  # last 20
                cooldowns = [
                    tid for tid, exp in maker_state.cooldowns.items()
                    if exp > time.time()
                ]
                total_fills = maker_state.total_fills
                total_cancels = maker_state.total_cancels
                realized_pnl = maker_state.realized_pnl

            # Quote uptime: fraction of 2s ticks where at least one quote is resting
            _uptime_samples += 1
            if live_orders:
                _quoted_samples += 1
            quote_uptime = _quoted_samples / _uptime_samples

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

            # ── Falcon intelligence snapshot ──────────────────────────────
            falcon_data = _build_falcon_snapshot(feeds, markets_snapshot, now)

            dash.update({
                "uptime_seconds": round(now - _start_time, 0),
                "inventory": {k: round(v, 2) for k, v in inventory.items()},
                "live_orders": live_orders,
                "cooldowns": cooldowns,
                "daily_pnl": round(mtm_pnl, 4),          # portfolio P&L: cash + open positions
                "cash_pnl": round(cash_pnl, 4),          # net cash flows
                "realized_pnl": round(realized_pnl, 4), # booked profit from closed round trips
                "total_abs_inventory": round(total_abs, 2),
                "active_markets": active_markets,
                "fills": fill_history,
                "n_active_markets": len(live_orders),
                "clob_age": _age("clob"),
                "micro_age": _age("microstructure"),
                "quote_uptime": round(quote_uptime, 3),
                "total_fills": total_fills,
                "total_cancels": total_cancels,
                "markout_stats": markout_tracker.stats if markout_tracker else {},
                "falcon_data": falcon_data,
            })
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(f"dashboard_loop error: {exc}")

        await asyncio.sleep(interval)


def _build_falcon_snapshot(feeds, markets_snapshot: dict, now: float) -> dict:
    """Build the Falcon intelligence dict pushed to the dashboard every tick."""

    # ── Elite whale trackers (Leaderboard + Wallet 360) ───────────────────
    wallets = []
    for stats in feeds.falcon_whale_stats.values():
        age_s = now - stats.fetched_at
        fresh = age_s < _WALLET_STALE_SECONDS
        wallets.append({
            "address": stats.wallet[:10] + "…" + stats.wallet[-6:] if len(stats.wallet) > 16 else stats.wallet,
            "pnl": round(stats.total_pnl, 0),
            "roi": round(stats.roi * 100, 1) if stats.roi < 10 else round(stats.roi, 1),  # handle % vs fraction
            "win_rate": round(stats.win_rate * 100, 1) if stats.win_rate <= 1.0 else round(stats.win_rate, 1),
            "sharpe": round(stats.sharpe_ratio, 2),
            "h_score": round(stats.h_score, 1),
            "trust_score": round(stats.trust_score, 3),
            "elite": stats.is_elite,
            "risk_level": stats.risk_level,
            "sybil": stats.sybil_risk_flag,
            "trend": stats.performance_trend,
            "trajectory": stats.trajectory,
            "rank": stats.leaderboard_rank,
            "fresh": fresh,
            "age": _fmt_age(age_s),
        })
    wallets.sort(key=lambda w: (w["elite"], w["h_score"]), reverse=True)

    # ── Active markets with Falcon insights ───────────────────────────────
    # Build condition_id → market lookup from active markets snapshot
    cid_to_cs = {cs.condition_id: cs for cs in markets_snapshot.values() if cs.condition_id}

    market_rows = []
    for cid, ins in feeds.falcon_market_insights.items():
        age_s = now - ins.fetched_at
        if age_s >= _INSIGHTS_STALE_SECONDS:
            continue
        in_active = cid in cid_to_cs
        spread_mult = compute_adverse_selection_penalty(cid, feeds)
        market_rows.append({
            "question": ins.question[:55] or ins.slug[:55],
            "condition_id": cid[:16],
            "volume_24h": round(ins.current_volume_24h, 0),
            "volume_trend": ins.volume_trend,
            "liquidity_tier": ins.liquidity_tier,
            "top1_pct": round(ins.top1_wallet_pct, 1),
            "top3_pct": round(ins.top3_wallet_pct, 1),
            "whale_flag": ins.whale_control_flag,
            "unique_traders": ins.unique_traders_7d,
            "spread_mult": round(spread_mult, 2),
            "in_active": in_active,  # True if we're currently quoting this market
            "age": _fmt_age(age_s),
        })

    # Sort: whale-flagged first, then by volume
    market_rows.sort(key=lambda m: (m["whale_flag"], m["volume_24h"]), reverse=True)

    # Feed age summary
    wallet_age = "—"
    if feeds.falcon_whale_stats:
        latest = max(s.fetched_at for s in feeds.falcon_whale_stats.values())
        wallet_age = _fmt_age(now - latest)

    insights_age = "—"
    if feeds.falcon_market_insights:
        latest = max(i.fetched_at for i in feeds.falcon_market_insights.values())
        insights_age = _fmt_age(now - latest)

    # Global spread multiplier: max across all active markets
    global_mult = 1.0
    for cs in markets_snapshot.values():
        m = compute_adverse_selection_penalty(cs.condition_id, feeds)
        if m > global_mult:
            global_mult = m

    return {
        "spread_multiplier": round(global_mult, 3),
        "wallets": wallets[:25],
        "markets": market_rows[:30],
        "wallet_feed_age": wallet_age,
        "insights_feed_age": insights_age,
        "n_whale_markets": sum(1 for m in market_rows if m["whale_flag"]),
        "n_spiking": sum(1 for m in market_rows if m["volume_trend"] == "Spiking"),
    }


def _fmt_age(seconds: float) -> str:
    s = int(seconds)
    if s < 0:
        return "?"
    if s < 60:
        return f"{s}s"
    return f"{s // 60}m{s % 60}s"
