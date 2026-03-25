"""Asyncio coroutines that snapshot AppState into DashboardState."""
import asyncio
import time

from market.state import AppState
from dashboard.state import DashboardState

_start_time = time.time()


async def dashboard_loop(state: AppState, dash: DashboardState, risk=None, interval: float = 2.0) -> None:
    while True:
        async with state._lock:
            feeds = state.feeds
            markets_snapshot = dict(state.markets)

        # Read positions from RiskManager (separate lock, outside state._lock)
        positions = []
        if risk is not None:
            async with risk._lock:
                for token_id, pos in risk.open_positions.items():
                    cs = markets_snapshot.get(token_id)
                    current_mid = cs.mid if cs else pos.entry_price
                    pnl = (current_mid - pos.entry_price) * (pos.size_usdc / pos.entry_price)
                    question = cs.question if cs else token_id[:16]
                    positions.append({
                        "token_id": token_id,
                        "question": question,
                        "side": "BUY_YES",
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
    # n_traded is cumulative lifetime total — add to existing, don't replace
    if "n_traded" in kwargs:
        current["n_traded"] = current.get("n_traded", 0) + kwargs.pop("n_traded")
    current.update(kwargs)
    dash.update({"scan_stats": current})
