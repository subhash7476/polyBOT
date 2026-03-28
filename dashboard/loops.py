"""Asyncio coroutines that snapshot AppState into DashboardState."""
import asyncio
import time

from market.state import AppState
from dashboard.state import DashboardState
from trading.positions import REDEEMED

_start_time = time.time()


async def dashboard_loop(state: AppState, dash: DashboardState, risk=None, interval: float = 2.0) -> None:
    while True:
        async with state._lock:
            feeds = state.feeds
            markets_snapshot = dict(state.markets)

        # Read positions from RiskManager (separate lock, outside state._lock)
        positions = []
        position_counts = {"open": 0, "resolved_pending_redeem": 0, "closed": 0}
        realized_pnl = consecutive_losses = 0.0
        open_count = 0
        open_exposure_usdc = 0.0
        pending_redeem_count = 0
        redeemed_count = 0
        realized_pnl_total = 0.0
        realized_pnl_today = 0.0
        lifetime_wins = 0
        lifetime_losses = 0
        if risk is not None:
            async with risk._lock:
                position_counts["open"] = len(risk.open_positions)
                position_counts["resolved_pending_redeem"] = len(getattr(risk, "pending_redemptions", {}))
                position_counts["closed"] = len(getattr(risk, "closed_positions", {}))
                realized_pnl = risk.daily_pnl
                consecutive_losses = risk.consecutive_losses

                # Lifecycle panel data
                open_count = len(risk.open_positions)
                open_exposure_usdc = sum(p.size_usdc for p in risk.open_positions.values())
                pending_redeem_count = len(getattr(risk, "pending_redemptions", {}))
                closed = getattr(risk, "closed_positions", {}) or {}
                pending = getattr(risk, "pending_redemptions", {}) or {}
                redeemed_count = sum(
                    1 for p in closed.values()
                    if getattr(p, "status", "") == REDEEMED
                )
                realized_pnl_today = risk.daily_pnl
                # Lifetime P&L and win/loss from closed + pending redemption positions
                for pos in list(closed.values()) + list(pending.values()):
                    payout = 0.0
                    if pos.resolved_yes is not None:
                        won = (pos.side == "BUY_YES" and pos.resolved_yes) or (pos.side == "BUY_NO" and not pos.resolved_yes)
                        payout = 1.0 if won else 0.0
                    pnl_pos = (payout - pos.entry_price) * (pos.size_usdc / max(pos.entry_price, 0.0001))
                    realized_pnl_total += pnl_pos
                    if pnl_pos > 0:
                        lifetime_wins += 1
                    elif pnl_pos < 0:
                        lifetime_losses += 1

                for token_id, pos in risk.open_positions.items():
                    cs = markets_snapshot.get(token_id)
                    current_mid = cs.mid if cs else pos.entry_price
                    if pos.side == "BUY_NO":
                        payout_mid = 1.0 - current_mid
                    else:
                        payout_mid = current_mid
                    pnl = (payout_mid - pos.entry_price) * (pos.size_usdc / pos.entry_price)
                    question = cs.question if cs else token_id[:16]
                    positions.append({
                        "token_id": token_id,
                        "question": question,
                        "side": pos.side,
                        "size_usdc": pos.size_usdc,
                        "entry_price": pos.entry_price,
                        "current_mid": current_mid,
                        "pnl_usdc": round(pnl, 2),
                        "opened_at": "",
                    })

        snapshot = {
            "uptime_seconds": time.time() - _start_time,
            "spot_prices": dict(feeds.spot_prices),
            "dvol": dict(feeds.dvol),
            "funding_rates": dict(feeds.funding_rates),
            "vol_skew": dict(feeds.vol_skew),
            "dxy": feeds.dxy,
            "dxy_confidence": getattr(feeds, "dxy_confidence", None),
            "yield_10y": getattr(feeds, "yield_10y", None),
            "fed_may_cut_prob": feeds.fed_may_cut_prob,
            "fed_expected_cuts": getattr(feeds, "fed_expected_cuts", None),
            "sofr": getattr(feeds, "sofr", None),
            "cpi": getattr(feeds, "cpi", None),
            "unrate": getattr(feeds, "unrate", None),
            "positions": positions,
            "position_counts": position_counts,
            "realized_pnl": realized_pnl,
            "consecutive_losses": int(consecutive_losses),
            "open_count": open_count,
            "open_exposure_usdc": open_exposure_usdc,
            "pending_redeem_count": pending_redeem_count,
            "redeemed_count": redeemed_count,
            "realized_pnl_total": realized_pnl_total,
            "realized_pnl_today": realized_pnl_today,
            "lifetime_wins": lifetime_wins,
            "lifetime_losses": lifetime_losses,
        }

        # Per-source feed timestamps: only update when data is freshly non-empty
        feed_ts = {}
        now = time.time()
        if snapshot["spot_prices"]:
            feed_ts["binance"] = now
        if snapshot["dvol"]:
            feed_ts["deribit"] = now
        if snapshot["dxy"] is not None or snapshot["sofr"] is not None:
            feed_ts["macro"] = now

        if feed_ts:
            snapshot["feed_updated_at"] = feed_ts

        dash.update(snapshot)
        await asyncio.sleep(interval)


def update_scan_stats(dash: DashboardState, **kwargs) -> None:
    current = dict(dash.scan_stats)
    # n_traded is per-scan; lifetime_trades is cumulative across the process lifetime.
    if "n_traded" in kwargs:
        traded = kwargs["n_traded"]
        current["lifetime_trades"] = current.get("lifetime_trades", 0) + traded
    current.update(kwargs)
    dash.update({"scan_stats": current})
