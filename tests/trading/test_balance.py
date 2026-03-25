import asyncio
import pytest
from unittest.mock import AsyncMock, patch
from market.state import AppState, BalanceState
from trading.balance import _extract_balance_value

def test_balance_state_session_pnl():
    bs = BalanceState(session_start=100.0, current=95.0)
    assert bs.session_pnl == pytest.approx(-5.0)

def test_balance_state_session_pnl_positive():
    bs = BalanceState(session_start=100.0, current=115.0)
    assert bs.session_pnl == pytest.approx(15.0)

def test_balance_state_default_zero():
    bs = BalanceState()
    assert bs.session_pnl == pytest.approx(0.0)

def test_appstate_has_balance():
    state = AppState()
    assert hasattr(state, "balance")
    assert isinstance(state.balance, BalanceState)

def test_extract_balance_value_from_list_shape():
    data = [{"user": "0xabc", "value": 12.5}]
    assert _extract_balance_value(data) == pytest.approx(12.5)

def test_extract_balance_value_from_portfolio_shape():
    data = {"portfolioValue": 42.0}
    assert _extract_balance_value(data) == pytest.approx(42.0)

def test_extract_balance_value_from_dict_value_shape():
    data = {"user": "0xabc", "value": 7.25}
    assert _extract_balance_value(data) == pytest.approx(7.25)

@pytest.mark.asyncio
async def test_balance_poller_sets_session_start_on_first_fetch():
    from trading.balance import BalancePoller
    state = AppState()
    poller = BalancePoller(state, wallet_address="0xABC", poll_interval=999)

    async def fake_fetch(wallet):
        return 200.0

    with patch.object(poller, "_fetch_usdc_balance", side_effect=fake_fetch):
        await poller._poll_once()

    assert state.balance.session_start == pytest.approx(200.0)
    assert state.balance.current == pytest.approx(200.0)

@pytest.mark.asyncio
async def test_balance_poller_does_not_overwrite_session_start():
    from trading.balance import BalancePoller
    state = AppState()
    state.balance.session_start = 100.0  # already set
    poller = BalancePoller(state, wallet_address="0xABC", poll_interval=999)

    async def fake_fetch(wallet):
        return 120.0

    with patch.object(poller, "_fetch_usdc_balance", side_effect=fake_fetch):
        await poller._poll_once()

    assert state.balance.session_start == pytest.approx(100.0)  # unchanged
    assert state.balance.current == pytest.approx(120.0)

@pytest.mark.asyncio
async def test_balance_poller_handles_fetch_failure_gracefully():
    from trading.balance import BalancePoller
    state = AppState()
    poller = BalancePoller(state, wallet_address="0xABC", poll_interval=999)

    async def failing_fetch(wallet):
        raise Exception("network down")

    with patch.object(poller, "_fetch_usdc_balance", side_effect=failing_fetch):
        # should not raise
        await poller._poll_once()

    assert state.balance.current == pytest.approx(0.0)
