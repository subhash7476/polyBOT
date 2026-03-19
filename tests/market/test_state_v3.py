import pytest
from market.state import FeedState, AppState


def test_feedstate_spot_prices_default_empty():
    fs = FeedState()
    assert fs.spot_prices == {}


def test_feedstate_set_and_get_spot():
    fs = FeedState()
    fs.spot_prices["BTC"] = 85000.0
    fs.spot_prices["SOL"] = 140.0
    assert fs.spot_prices["BTC"] == 85000.0
    assert fs.spot_prices["SOL"] == 140.0


def test_feedstate_dvol_default_empty():
    fs = FeedState()
    assert fs.dvol == {}


def test_feedstate_funding_rates_default_empty():
    fs = FeedState()
    assert fs.funding_rates == {}


def test_feedstate_vol_skew_default_empty():
    fs = FeedState()
    assert fs.vol_skew == {}


def test_feedstate_backward_compat_btc():
    fs = FeedState()
    fs.spot_prices["BTC"] = 85000.0
    fs.dvol["BTC"] = 72.0
    assert fs.btc_price == 85000.0
    assert fs.btc_dvol == 72.0


def test_feedstate_backward_compat_eth():
    fs = FeedState()
    fs.spot_prices["ETH"] = 3200.0
    fs.dvol["ETH"] = 65.0
    assert fs.eth_price == 3200.0
    assert fs.eth_dvol == 65.0


def test_feedstate_backward_compat_returns_none_when_missing():
    fs = FeedState()
    assert fs.btc_price is None
    assert fs.eth_dvol is None


def test_feedstate_btc_funding_rate_compat():
    fs = FeedState()
    fs.funding_rates["BTC"] = 0.0005
    assert fs.btc_funding_rate == 0.0005


def test_feedstate_btc_vol_skew_compat():
    fs = FeedState()
    fs.vol_skew["BTC"] = 3.5
    assert fs.btc_vol_skew == 3.5


@pytest.mark.asyncio
async def test_appstate_update_asset_feed_spot():
    state = AppState()
    await state.update_asset_feed("SOL", spot=140.0)
    assert state.feeds.spot_prices["SOL"] == 140.0


@pytest.mark.asyncio
async def test_appstate_update_asset_feed_all():
    state = AppState()
    await state.update_asset_feed("BTC", spot=85000.0, dvol=72.0,
                                  funding_rate=0.0001, vol_skew=2.5)
    assert state.feeds.spot_prices["BTC"] == 85000.0
    assert state.feeds.dvol["BTC"] == 72.0
    assert state.feeds.funding_rates["BTC"] == 0.0001
    assert state.feeds.vol_skew["BTC"] == 2.5


@pytest.mark.asyncio
async def test_appstate_update_feeds_still_works():
    """Legacy update_feeds() for backward compat."""
    state = AppState()
    await state.update_feeds(dxy_trend=-0.02, dxy_confidence=0.8)
    assert state.feeds.dxy_trend == -0.02
    assert state.feeds.dxy_confidence == 0.8
