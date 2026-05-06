from datetime import datetime, timezone, timedelta
from market.market_screener import score_market, rank_markets, ScreenedMarket
from market.state import ContractState


def _cs(question, volume=100000, bid=0.48, ask=0.52, category="crypto"):
    return ContractState(
        yes_token_id="tok1", no_token_id="tok2",
        question=question, category=category,
        best_bid=bid, best_ask=ask, volume_usd=volume,
    )


def test_score_market_returns_float():
    cs = _cs("Will BTC > $100k by March?")
    expiry = datetime.now(timezone.utc) + timedelta(hours=48)
    score = score_market(cs, expiry=expiry, is_parseable=True)
    assert isinstance(score, float)
    assert score >= 0


def test_higher_volume_scores_higher():
    expiry = datetime.now(timezone.utc) + timedelta(hours=48)
    low_vol = _cs("Will BTC > $100k?", volume=1000)
    high_vol = _cs("Will BTC > $100k?", volume=1000000)
    s_low = score_market(low_vol, expiry=expiry, is_parseable=True)
    s_high = score_market(high_vol, expiry=expiry, is_parseable=True)
    assert s_high > s_low


def test_parseable_scores_higher_than_not():
    expiry = datetime.now(timezone.utc) + timedelta(hours=48)
    cs = _cs("Will BTC > $100k?", volume=50000)
    s_parseable = score_market(cs, expiry=expiry, is_parseable=True)
    s_not = score_market(cs, expiry=expiry, is_parseable=False)
    assert s_parseable > s_not


def test_narrow_spread_scores_higher():
    expiry = datetime.now(timezone.utc) + timedelta(hours=48)
    narrow = _cs("Will BTC > $100k?", bid=0.49, ask=0.51)
    wide = _cs("Will BTC > $100k?", bid=0.30, ask=0.70)
    s_narrow = score_market(narrow, expiry=expiry, is_parseable=True)
    s_wide = score_market(wide, expiry=expiry, is_parseable=True)
    assert s_narrow > s_wide


def test_rank_markets_returns_sorted_list():
    expiry = datetime.now(timezone.utc) + timedelta(hours=48)
    markets = {
        "tok1": (_cs("Will BTC > $100k?", volume=100000), expiry, True),
        "tok2": (_cs("Will ETH > $5k?", volume=5000), expiry, True),
        "tok3": (_cs("Will something happen?", volume=50000), expiry, False),
    }
    ranked = rank_markets(markets)
    assert len(ranked) == 3
    assert isinstance(ranked[0], ScreenedMarket)
    assert ranked[0].score >= ranked[1].score


def test_rank_markets_flags_staleness():
    stale_expiry = datetime.now(timezone.utc) + timedelta(hours=24)
    cs = _cs("Will BTC > $100k?", volume=10)
    markets = {"tok1": (cs, stale_expiry, True)}
    ranked = rank_markets(markets)
    assert ranked[0].stale_flag is True


def test_score_market_no_expiry():
    cs = _cs("Will BTC > $100k?", volume=50000)
    score = score_market(cs, expiry=None, is_parseable=True)
    assert isinstance(score, float)
    assert score >= 0
