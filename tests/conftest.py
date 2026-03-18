import asyncio
import pytest
from market.state import AppState, FeedState, ContractState


@pytest.fixture
def app_state():
    return AppState()


@pytest.fixture
def feed_state():
    return FeedState(
        btc_dvol=60.0, eth_dvol=55.0,
        btc_price=84000.0, eth_price=3200.0,
        btc_funding_rate=0.0001,
        btc_exchange_netflow=0.4,
        dxy=104.0, dxy_confidence=1.0,
        yield_10y=4.2, yield_10y_confidence=1.0,
        fed_may_cut_prob=0.35, fed_confidence=1.0,
    )


@pytest.fixture
def sample_contract():
    return ContractState(
        yes_token_id="yes123",
        no_token_id="no123",
        question="Will BTC be above $90,000 by end of March?",
        category="crypto",
        best_bid=0.30,
        best_ask=0.34,
        volume_usd=50_000,
    )


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
