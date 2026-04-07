"""Tests for engine/falcon_signals.py"""

import time
import pytest
from engine.falcon_signals import compute_adverse_selection_penalty, compute_market_skew_adjustment
from market.state import FeedState, FalconWalletStats, FalconMarketInsight


def _fresh_insight(**kwargs) -> FalconMarketInsight:
    defaults = dict(
        condition_id="0xcond1",
        question="Will BTC hit $100k?",
        volume_trend="Normal",
        top1_wallet_pct=30.0,
        whale_control_flag=False,
        trade_concentration_flag=False,
        fetched_at=time.time(),
    )
    defaults.update(kwargs)
    return FalconMarketInsight(**defaults)


def _fresh_wallet(**kwargs) -> FalconWalletStats:
    defaults = dict(
        wallet="0xabc",
        win_rate=0.80,
        roi=0.60,
        max_drawdown=-0.05,
        sybil_risk_flag=False,
        fetched_at=time.time(),
    )
    defaults.update(kwargs)
    return FalconWalletStats(**defaults)


# ── Adverse selection penalty ─────────────────────────────────────────────────

def test_penalty_no_data_returns_one():
    assert compute_adverse_selection_penalty("0xcond", FeedState()) == 1.0


def test_penalty_whale_control_below_threshold_returns_one(monkeypatch):
    import config
    monkeypatch.setattr(config, "FALCON_WHALE_CONTROL_PCT", 60.0)
    feeds = FeedState()
    feeds.falcon_market_insights["0xcond"] = _fresh_insight(
        condition_id="0xcond", top1_wallet_pct=55.0, whale_control_flag=False
    )
    assert compute_adverse_selection_penalty("0xcond", feeds) == 1.0


def test_penalty_whale_control_above_threshold(monkeypatch):
    import config
    monkeypatch.setattr(config, "FALCON_WHALE_CONTROL_PCT", 60.0)
    feeds = FeedState()
    feeds.falcon_market_insights["0xcond"] = _fresh_insight(
        condition_id="0xcond", top1_wallet_pct=75.0, whale_control_flag=True,
        trade_concentration_flag=False
    )
    result = compute_adverse_selection_penalty("0xcond", feeds)
    assert result == 1.5


def test_penalty_whale_plus_concentration_gives_max(monkeypatch):
    import config
    monkeypatch.setattr(config, "FALCON_WHALE_CONTROL_PCT", 60.0)
    feeds = FeedState()
    feeds.falcon_market_insights["0xcond"] = _fresh_insight(
        condition_id="0xcond", top1_wallet_pct=80.0, whale_control_flag=True,
        trade_concentration_flag=True
    )
    assert compute_adverse_selection_penalty("0xcond", feeds) == 2.0


def test_penalty_stale_insight_falls_back_to_global(monkeypatch):
    import config
    monkeypatch.setattr(config, "FALCON_WHALE_CONTROL_PCT", 60.0)
    monkeypatch.setattr(config, "FALCON_MARKET_INSIGHTS_POLL_INTERVAL", 120)
    feeds = FeedState()
    # Stale insight with whale flag
    feeds.falcon_market_insights["0xcond"] = _fresh_insight(
        condition_id="0xcond", top1_wallet_pct=75.0, whale_control_flag=True,
        fetched_at=time.time() - 99999
    )
    # But no elite wallet → fall back to 1.0
    assert compute_adverse_selection_penalty("0xcond", feeds) == 1.0


def test_penalty_elite_wallet_global_fallback(monkeypatch):
    import config
    monkeypatch.setattr(config, "FALCON_WHALE_CONTROL_PCT", 60.0)
    monkeypatch.setattr(config, "FALCON_WALLET_POLL_INTERVAL", 300)
    feeds = FeedState()
    # No per-market insight, but elite wallet present
    # trust = 0.85 * 1.80 * 0.95 = 1.45 — not quite elite (need >1.5)
    # use higher roi to push above 1.5: 0.85 * (1+1.2) * 0.95 = 0.85*2.2*0.95 = 1.775
    feeds.falcon_whale_stats["0xabc"] = _fresh_wallet(
        win_rate=0.85, roi=1.2, max_drawdown=-0.05, sybil_risk_flag=False
    )
    result = compute_adverse_selection_penalty("0xcond", feeds)
    assert result == 1.3  # global elite fallback


def test_penalty_sybil_wallet_not_elite():
    feeds = FeedState()
    # High ROI but sybil flag → trust_score halved → not elite
    feeds.falcon_whale_stats["0xabc"] = _fresh_wallet(
        win_rate=0.90, roi=2.0, max_drawdown=-0.02, sybil_risk_flag=True
    )
    # trust = 0.90 * 3.0 * 0.98 * 0.5 (sybil) = 1.323 < 1.5 → not elite
    assert compute_adverse_selection_penalty("0xcond", feeds) == 1.0


# ── Market skew adjustment ────────────────────────────────────────────────────

def test_skew_no_data_returns_zero():
    assert compute_market_skew_adjustment("0xcond", FeedState()) == 0.0


def test_skew_normal_trend_returns_zero():
    feeds = FeedState()
    feeds.falcon_market_insights["0xcond"] = _fresh_insight(volume_trend="Normal")
    assert compute_market_skew_adjustment("0xcond", feeds) == 0.0


def test_skew_dying_interest_returns_positive():
    feeds = FeedState()
    feeds.falcon_market_insights["0xcond"] = _fresh_insight(volume_trend="Dying Interest")
    adj = compute_market_skew_adjustment("0xcond", feeds)
    assert adj > 0


def test_skew_spiking_returns_zero():
    feeds = FeedState()
    feeds.falcon_market_insights["0xcond"] = _fresh_insight(volume_trend="Spiking")
    assert compute_market_skew_adjustment("0xcond", feeds) == 0.0


def test_skew_stale_returns_zero(monkeypatch):
    import config
    monkeypatch.setattr(config, "FALCON_MARKET_INSIGHTS_POLL_INTERVAL", 120)
    feeds = FeedState()
    feeds.falcon_market_insights["0xcond"] = _fresh_insight(
        volume_trend="Dying Interest", fetched_at=time.time() - 99999
    )
    assert compute_market_skew_adjustment("0xcond", feeds) == 0.0
