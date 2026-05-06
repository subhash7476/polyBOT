"""
engine/falcon_signals.py

Falcon-derived parameters for the QuoteEngine.

  compute_adverse_selection_penalty(condition_id, feeds) → spread_multiplier (1.0–2.0)
      Uses Market Insights whale-control data for the specific market.
      If a whale controls >FALCON_WHALE_CONTROL_PCT of a market's volume,
      that market has informed flow — widen spread to reduce adverse selection.
      Falls back to global elite-wallet presence if no per-market data.

  compute_market_skew_adjustment(condition_id, feeds) → model_adj (-0.02 – +0.02)
      Nudges fair value based on volume trend.
      "Spiking" volume = fresh informed flow, slight mean-reversion nudge.
      "Dying Interest" = stale market, slight push toward 0.5.
      Zero when data is absent or trend is Normal.

Both return neutral values (1.0 / 0.0) when no fresh Falcon data is available,
so the QuoteEngine degrades gracefully before credentials are set.
"""

import time
import numpy as np
from typing import Optional

from market.state import FeedState
import config
from utils.logger import get_logger

log = get_logger(__name__)

# Freshness gates
_INSIGHTS_STALE_SECONDS = config.FALCON_MARKET_INSIGHTS_POLL_INTERVAL * 3
_WALLET_STALE_SECONDS   = config.FALCON_WALLET_POLL_INTERVAL * 3

# Trust threshold for elite-wallet global widening (fallback path)
_HIGH_TRUST_THRESHOLD = 1.5


# ── Adverse selection penalty ─────────────────────────────────────────────────

def compute_adverse_selection_penalty(condition_id: str, feeds: FeedState) -> float:
    """
    Returns spread_multiplier ∈ [1.0, 2.0].

    Primary path — per-market whale control (Market Insights, agent 575):
      If top-1 wallet holds > FALCON_WHALE_CONTROL_PCT of a market's volume,
      return 1.5 (widen 50%).  If also trade_concentration_flag, return 2.0.

    Fallback path — global elite wallet presence (Wallet 360, agent 581):
      If any tracked wallet is elite (trust_score > 1.5) and data is fresh,
      return 1.3 (mild widening — we know smart money is active somewhere).

    Returns 1.0 when no relevant fresh data is available.
    """
    now = time.time()

    # Primary: per-market whale control
    if condition_id and feeds.falcon_market_insights:
        insight = feeds.falcon_market_insights.get(condition_id)
        if insight and (now - insight.fetched_at) < _INSIGHTS_STALE_SECONDS:
            pct = insight.top1_wallet_pct
            if pct >= config.FALCON_WHALE_CONTROL_PCT:
                multiplier = 2.0 if insight.trade_concentration_flag else 1.5
                log.debug(
                    f"adverse_selection [{condition_id[:8]}]: "
                    f"whale_pct={pct:.1f}% → multiplier={multiplier}"
                )
                return multiplier

    # Fallback: any elite wallet tracked globally
    if feeds.falcon_whale_stats:
        for stats in feeds.falcon_whale_stats.values():
            if (now - stats.fetched_at) < _WALLET_STALE_SECONDS and stats.is_elite:
                log.debug("adverse_selection: elite wallet active globally → 1.3×")
                return 1.3

    return 1.0


# ── Market skew adjustment ────────────────────────────────────────────────────

def compute_market_skew_adjustment(condition_id: str, feeds: FeedState) -> float:
    """
    Returns model_adj ∈ [−0.02, +0.02].

    Uses volume trend from Market Insights to nudge fair value:
      "Spiking"       → informed buying likely → slight push toward current mid
                         (adj = 0: we trust the mid; no directional bet)
      "Dying Interest"→ market going stale → nudge fair value toward 0.5
                         (adj is signed away from 0.5 to reduce quoting near extremes)
      "Declining"     → mild version of dying interest
      "Normal"        → 0.0

    Returns 0.0 when data is absent or stale.
    """
    if not condition_id or not feeds.falcon_market_insights:
        return 0.0

    now = time.time()
    insight = feeds.falcon_market_insights.get(condition_id)
    if insight is None or (now - insight.fetched_at) >= _INSIGHTS_STALE_SECONDS:
        return 0.0

    trend = insight.volume_trend
    if trend == "Dying Interest":
        return 0.01   # tiny nudge toward 0.5 (reduces inventory buildup near edges)
    if trend == "Declining":
        return 0.005
    # Spiking / Normal / No Trades → no directional adjustment
    return 0.0


# ── Dashboard helpers (called from dashboard_loop) ────────────────────────────

def get_elite_wallets(feeds: FeedState) -> list[dict]:
    """Return fresh elite wallets sorted by trust_score desc, for the dashboard."""
    now = time.time()
    out = []
    for stats in feeds.falcon_whale_stats.values():
        if (now - stats.fetched_at) < _WALLET_STALE_SECONDS:
            out.append(stats)
    out.sort(key=lambda s: s.trust_score, reverse=True)
    return out


def get_whale_controlled_markets(feeds: FeedState) -> list[dict]:
    """Return markets with whale_control_flag=True, sorted by top1_wallet_pct desc."""
    now = time.time()
    out = []
    for ins in feeds.falcon_market_insights.values():
        if (now - ins.fetched_at) < _INSIGHTS_STALE_SECONDS and ins.whale_control_flag:
            out.append(ins)
    out.sort(key=lambda i: i.top1_wallet_pct, reverse=True)
    return out


def get_spiking_markets(feeds: FeedState) -> list[dict]:
    """Return markets with volume_trend='Spiking', sorted by volume desc."""
    now = time.time()
    out = []
    for ins in feeds.falcon_market_insights.values():
        if (now - ins.fetched_at) < _INSIGHTS_STALE_SECONDS and ins.volume_trend == "Spiking":
            out.append(ins)
    out.sort(key=lambda i: i.current_volume_24h, reverse=True)
    return out
