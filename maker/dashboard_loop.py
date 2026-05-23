"""Asyncio loop that snapshots MakerState -> MakerDashboardState every 2s."""
import asyncio
import time
from datetime import datetime as _dt, timezone as _tz
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


def _days_left(end_date_iso: str | None) -> float | None:
    """Return days until end_date_iso from now. Negative = expired. None = unknown."""
    if not end_date_iso:
        return None
    try:
        end = _dt.fromisoformat(end_date_iso.replace("Z", "+00:00"))
        return (end.timestamp() - _dt.now(_tz.utc).timestamp()) / 86400.0
    except ValueError:
        return None


def _rebate_snapshot(cs, levels: list[dict] | None = None) -> dict:
    """Summarise maker-rebate eligibility for a market and its current quote."""
    levels = levels or []
    center = levels[len(levels) // 2] if levels else {}
    bid = float(center.get("bid_price", 0.0) or 0.0)
    ask = float(center.get("ask_price", 0.0) or 0.0)
    size = max(
        float(center.get("bid_size", 0.0) or 0.0),
        float(center.get("ask_size", 0.0) or 0.0),
    )
    spread = max(0.0, ask - bid)
    half_spread = spread / 2.0

    fees_enabled = bool(getattr(cs, "fees_enabled", False)) if cs else False
    min_size = round(float(getattr(cs, "min_incentive_size", 0.0) or 0.0), 2) if cs else 0.0
    max_spread = round(float(getattr(cs, "max_incentive_spread", 0.0) or 0.0), 4) if cs else 0.0
    has_reward_params = min_size > 0.0 and max_spread > 0.0

    rebate_eligible = bool(
        cs
        and fees_enabled
        and has_reward_params
        and size >= min_size
        and half_spread <= max_spread
    )
    if rebate_eligible:
        rebate_status = "eligible"
    elif not cs:
        rebate_status = "unknown"
    elif not levels:
        if not fees_enabled:
            rebate_status = "fee_free"
        elif not has_reward_params:
            rebate_status = "no_reward_params"
        else:
            rebate_status = "reward_ready"
    elif not fees_enabled:
        rebate_status = "fee_free"
    elif not has_reward_params:
        rebate_status = "no_reward_params"
    elif size < min_size:
        rebate_status = "size_below_min"
    else:
        rebate_status = "spread_too_wide"

    return {
        "fees_enabled": fees_enabled,
        "min_incentive_size": min_size,
        "max_incentive_spread": max_spread,
        "quote_size": round(size, 2) if levels else None,
        "quote_half_spread": round(half_spread, 4) if levels else None,
        "rebate_eligible": rebate_eligible,
        "rebate_status": rebate_status,
    }


async def maker_dashboard_loop(
    app_state: AppState,
    maker_state: MakerState,
    dash: MakerDashboardState,
    interval: float = 2.0,
    markout_tracker: "MarkoutTracker | None" = None,
) -> None:
    _uptime_samples = 0
    _quoted_samples = 0
    _arb_total_events = 0

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
                session_by_market = dict(maker_state.session_by_market)
                pre_mid_fills = maker_state.session_pre_midnight_fills
                pre_mid_cash = maker_state.session_pre_midnight_cash
                pre_mid_realized = maker_state.session_pre_midnight_realized
                today_stats = dict(maker_state.today_stats)
                lifetime_stats = dict(maker_state.lifetime_stats)
                selected_token_ids = set(maker_state.selected_token_ids)

            # Quote uptime: fraction of 2s ticks where at least one quote is resting
            _uptime_samples += 1
            if live_orders:
                _quoted_samples += 1
            quote_uptime = _quoted_samples / _uptime_samples

            total_abs = sum(abs(v) for v in inventory.values())

            # Mark-to-market P&L: cash flows + current value of open positions
            mtm_pnl = maker_state.mtm_pnl(markets_snapshot)

            # USDC currently deployed: gross position value at current mid prices
            current_investment = sum(
                abs(net) * (markets_snapshot[tid].mid if tid in markets_snapshot else 0.0)
                for tid, net in inventory.items()
                if net != 0.0
            )

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
                rebate = _rebate_snapshot(cs, levels)
                active_markets.append({
                    "token_id": token_id[:16],
                    "question": question,
                    "bid": bid_p,
                    "ask": ask_p,
                    "spread": spread,
                    "n_levels": len(levels),
                    "inventory": round(inv, 2),
                    "cooldown": in_cd,
                    "end_date_iso": cs.end_date_iso if cs else None,
                    "days_left": _days_left(cs.end_date_iso if cs else None),
                    "volume_24h": (getattr(cs, 'volume_24h', None) or cs.volume_usd) if cs else None,
                    "book_bid": cs.best_bid if cs else None,
                    "book_ask": cs.best_ask if cs else None,
                    "bid_depth": getattr(cs, 'bid_depth', 0.0) if cs else 0.0,
                    "ask_depth": getattr(cs, 'ask_depth', 0.0) if cs else 0.0,
                    "category": cs.category if cs else None,
                    **rebate,
                })

            # Build selected markets list (all markets chosen by MarketSelector)
            selected_markets = []
            for token_id in selected_token_ids:
                cs = markets_snapshot.get(token_id)
                if cs is None:
                    continue
                dl = _days_left(cs.end_date_iso)
                rebate = _rebate_snapshot(cs, live_orders.get(token_id))
                selected_markets.append({
                    "token_id": token_id[:16],
                    "question": cs.question[:70],
                    "category": cs.category,
                    "book_bid": cs.best_bid,
                    "book_ask": cs.best_ask,
                    "spread": round(cs.best_ask - cs.best_bid, 4),
                    "volume_24h": getattr(cs, 'volume_24h', None) or cs.volume_usd,
                    "days_left": dl,
                    "is_quoting": token_id in live_orders,
                    **rebate,
                })
            # Sort: QUOTING first, then by volume descending
            selected_markets.sort(key=lambda m: (not m["is_quoting"], -(m["volume_24h"] or 0)))

            # Feed ages
            now = time.time()

            def _age(name):
                ts = feed_stamps.get(name, 0)
                if ts == 0:
                    return "?"
                s = int(now - ts)
                return f"{s}s" if s < 60 else f"{s // 60}m{s % 60}s"

            # ── Three-timeframe P&L ───────────────────────────────────────
            # session_by_market holds fills since last UTC midnight only.
            # pre_mid_* holds fills from earlier in this runtime (folded in at midnight).
            sbm_fills = sum(m.get("fills", 0) for m in session_by_market.values())
            sbm_cash = sum(m.get("cash_pnl", 0.0) for m in session_by_market.values())
            sbm_realized = sum(m.get("realized_pnl", 0.0) for m in session_by_market.values())

            # SESSION: full runtime = pre-midnight totals + today's fills
            sess_fills = pre_mid_fills + sbm_fills
            sess_cash = pre_mid_cash + sbm_cash
            sess_realized = pre_mid_realized + sbm_realized
            sess_position_value = sum(
                net * (markets_snapshot[tid].mid if tid in markets_snapshot else 0.0)
                for tid, net in inventory.items()
            )
            sess_mtm = sess_cash + sess_position_value

            # TODAY: pre-session ledger snapshot (fills before this startup) + today's session fills
            today_fills = today_stats.get("fills", 0) + sbm_fills
            today_cash = today_stats.get("cash_pnl", 0.0) + sbm_cash
            today_realized = today_stats.get("realized_pnl", 0.0) + sbm_realized

            # ALL TIME: lifetime (days before today) + today
            lt_fills = lifetime_stats.get("total_fills", 0)
            lt_cash = lifetime_stats.get("total_cash_pnl", 0.0)
            lt_realized = lifetime_stats.get("total_realized_pnl", 0.0)
            alltime_fills = lt_fills + today_fills
            alltime_cash = lt_cash + today_cash
            alltime_realized = lt_realized + today_realized

            # Per-market: merge lifetime + today + session
            by_market = _merge_market_stats(
                lifetime_stats.get("by_market", {}),
                today_stats.get("by_market", {}),
                session_by_market,
            )

            # ── Live P&L: markets currently being quoted ──────────────────────────
            by_market_lookup = {row["token_id"]: row for row in by_market}
            live_positions = []
            for token_id, levels in live_orders.items():
                cs  = markets_snapshot.get(token_id)
                dl  = _days_left(cs.end_date_iso if cs else None)
                # Skip if end date clearly passed (resolved)
                if dl is not None and dl < -0.1:
                    continue
                inv      = inventory.get(token_id, 0.0)
                mid      = cs.mid if cs else 0.0
                pos_val  = inv * mid
                tid16    = token_id[:16]
                row      = by_market_lookup.get(tid16, {})
                cash     = row.get("alltime_cash_pnl", 0.0)
                realized = row.get("alltime_realized_pnl", 0.0)
                center   = levels[len(levels) // 2] if levels else {}
                rebate   = _rebate_snapshot(cs, levels)
                live_positions.append({
                    "token_id":       tid16,
                    "question":       cs.question[:70] if cs else tid16,
                    "end_date_iso":   cs.end_date_iso if cs else None,
                    "days_left":      dl,
                    "category":       cs.category if cs else None,
                    "inventory":      round(inv, 2),
                    "current_mid":    round(mid, 4),
                    "bid":            round(center.get("bid_price", 0.0), 4),
                    "ask":            round(center.get("ask_price", 0.0), 4),
                    "position_value": round(pos_val, 4),
                    "cash_pnl":       round(cash, 4),
                    "realized_pnl":   round(realized, 4),
                    "mtm_pnl":        round(cash + pos_val, 4),
                    **rebate,
                })
            live_positions.sort(key=lambda p: abs(p["mtm_pnl"]), reverse=True)

            # ── Resolved markets: historical P&L from fill logs ────────────────────
            tid16_to_full    = {tid[:16]: tid for tid in markets_snapshot}
            live_tid16s      = {tid[:16] for tid in live_orders}
            resolved_markets = []
            for row in by_market:
                tid16    = row["token_id"]
                if tid16 in live_tid16s:
                    continue  # still being quoted
                full_tid = tid16_to_full.get(tid16)
                cs       = markets_snapshot.get(full_tid) if full_tid else None
                dl       = _days_left(cs.end_date_iso if cs else None)
                # Resolved: past end date OR no longer tracked by CLOBMonitor
                if not (cs is None or (dl is not None and dl < 0)):
                    continue
                resolved_markets.append({
                    "token_id":     tid16,
                    "question":     row.get("question") or (cs.question[:70] if cs else tid16),
                    "end_date_iso": cs.end_date_iso if cs else None,
                    "days_left":    dl,
                    "category":     cs.category if cs else None,
                    "fills":        row.get("alltime_fills", 0),
                    "cash_pnl":     round(row.get("alltime_cash_pnl", 0.0), 4),
                    "realized_pnl": round(row.get("alltime_realized_pnl", 0.0), 4),
                })
            resolved_markets.sort(key=lambda r: r["fills"], reverse=True)
            resolved_markets = resolved_markets[:150]

            # ── Session P&L by market: ALL markets with fills this session ──────────
            session_pnl_list = []
            for token_id, mkt in session_by_market.items():
                cs      = markets_snapshot.get(token_id)
                inv     = inventory.get(token_id, 0.0)
                mid     = cs.mid if cs else 0.0
                pos_val = inv * mid
                cash    = mkt.get("cash_pnl", 0.0)
                realized = mkt.get("realized_pnl", 0.0)
                session_pnl_list.append({
                    "token_id":       token_id[:16],
                    "question":       mkt.get("question") or (cs.question[:70] if cs else token_id[:16]),
                    "end_date_iso":   cs.end_date_iso if cs else None,
                    "days_left":      _days_left(cs.end_date_iso if cs else None),
                    "category":       cs.category if cs else None,
                    "inventory":      round(inv, 2),
                    "current_mid":    round(mid, 4),
                    "position_value": round(pos_val, 4),
                    "cash_pnl":       round(cash, 4),
                    "realized_pnl":   round(realized, 4),
                    "mtm_pnl":        round(cash + pos_val, 4),
                    "is_quoting":     token_id in live_orders,
                })
            session_pnl_list.sort(key=lambda p: abs(p["realized_pnl"]), reverse=True)

            # ── Arb scanner: find condition_id pairs where YES_ask + NO_ask < 1.0 ─
            arb_current, n_new_arb = _scan_arb(markets_snapshot)
            _arb_total_events += n_new_arb

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
                "current_investment": round(current_investment, 2),
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
                "session_fills": sess_fills,
                "session_cash_pnl": round(sess_cash, 4),
                "session_realized_pnl": round(sess_realized, 4),
                "session_mtm_pnl": round(sess_mtm, 4),
                "today_fills": today_fills,
                "today_cash_pnl": round(today_cash, 4),
                "today_realized_pnl": round(today_realized, 4),
                "alltime_fills": alltime_fills,
                "alltime_cash_pnl": round(alltime_cash, 4),
                "alltime_realized_pnl": round(alltime_realized, 4),
                "by_market": by_market,
                "selected_markets": selected_markets,
                "arb_total_events": _arb_total_events,
                "arb_current": arb_current,
                "live_positions": live_positions,
                "resolved_markets": resolved_markets,
                "session_pnl": session_pnl_list,
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


def _merge_market_stats(
    lt_by_market: dict,
    today_by_market: dict,
    session_by_market: dict,
) -> list:
    """Merge lifetime + today + session per-market dicts into a sorted list for the dashboard."""
    merged: dict[str, dict] = {}
    for src in (lt_by_market, today_by_market, session_by_market):
        for tid, m in src.items():
            row = merged.setdefault(tid, {
                "token_id": tid[:16],
                "question": "",
                "alltime_fills": 0,
                "alltime_cash_pnl": 0.0,
                "alltime_realized_pnl": 0.0,
            })
            row["alltime_fills"] += m.get("fills", 0)
            row["alltime_cash_pnl"] += m.get("cash_pnl", 0.0)
            row["alltime_realized_pnl"] += m.get("realized_pnl", 0.0)
            if m.get("question"):
                row["question"] = m["question"]

    for row in merged.values():
        row["alltime_cash_pnl"] = round(row["alltime_cash_pnl"], 4)
        row["alltime_realized_pnl"] = round(row["alltime_realized_pnl"], 4)

    return sorted(merged.values(), key=lambda r: r["alltime_fills"], reverse=True)[:50]


def _scan_arb(markets_snapshot: dict) -> tuple[list, int]:
    """Scan for arbitrage: group by condition_id, find pairs where YES_ask + NO_ask < 1.0.

    Requires CLOBMonitor to track both YES and NO tokens for the same market.
    Each pair in markets_snapshot with the same condition_id is treated as YES/NO sides.
    Returns (opportunities_list, n_new_events).
    """
    from collections import defaultdict
    groups: dict[str, list] = defaultdict(list)
    for token_id, cs in markets_snapshot.items():
        if cs.condition_id and cs.best_ask > 0 and cs.best_bid > 0:
            groups[cs.condition_id].append(cs)

    opps = []
    n_events = 0
    for cid, tokens in groups.items():
        if len(tokens) == 2:
            cs_a, cs_b = tokens[0], tokens[1]
            ask_sum = cs_a.best_ask + cs_b.best_ask
            if 0 < ask_sum < 1.0:
                gap = round(1.0 - ask_sum, 4)
                question = cs_a.question or cs_b.question or cid
                opps.append({
                    "question": question[:60],
                    "condition_id": cid[:16],
                    "ask_a": round(cs_a.best_ask, 4),
                    "ask_b": round(cs_b.best_ask, 4),
                    "ask_sum": round(ask_sum, 4),
                    "gap": gap,
                })
                n_events += 1
    opps.sort(key=lambda x: -x["gap"])
    return opps, n_events


def _fmt_age(seconds: float) -> str:
    s = int(seconds)
    if s < 0:
        return "?"
    if s < 60:
        return f"{s}s"
    return f"{s // 60}m{s % 60}s"
