import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from monitoring.alerts import AlertManager, AlertType


def test_alert_type_enum():
    assert AlertType.TRADE_EXECUTED
    assert AlertType.ARB_DETECTED
    assert AlertType.DAILY_SUMMARY
    assert AlertType.FEED_DISCONNECTED
    assert AlertType.RISK_LIMIT_APPROACHING
    assert AlertType.FLATLINE_DETECTED


def test_alert_manager_init_no_config():
    mgr = AlertManager(bot_token="", chat_id="")
    assert mgr is not None
    assert not mgr.enabled


def test_alert_manager_init_with_config():
    mgr = AlertManager(bot_token="123:ABC", chat_id="456")
    assert mgr.enabled


def test_format_trade_message():
    mgr = AlertManager(bot_token="123:ABC", chat_id="456")
    msg = mgr.format_trade(
        side="BUY_YES", question="Will BTC > $100k?",
        size=25.0, model_prob=0.72, market_mid=0.65, ev=0.07
    )
    assert "BUY_YES" in msg
    assert "25.0" in msg
    assert "BTC" in msg


def test_format_arb_message():
    mgr = AlertManager(bot_token="123:ABC", chat_id="456")
    msg = mgr.format_arb(description="BUY YES BTC>$100k, BUY NO BTC>$90k", spread=0.05)
    assert "0.05" in msg or "5" in msg
    assert "ARB" in msg.upper() or "arb" in msg.lower()


@pytest.mark.asyncio
async def test_send_alert_disabled_when_no_token():
    mgr = AlertManager(bot_token="", chat_id="")
    result = await mgr.send(AlertType.TRADE_EXECUTED, "test message")
    assert result is False


@pytest.mark.asyncio
async def test_send_alert_calls_telegram_api():
    mgr = AlertManager(bot_token="123:ABC", chat_id="456")
    with patch("monitoring.alerts.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client.post = AsyncMock(return_value=mock_resp)
        result = await mgr.send(AlertType.TRADE_EXECUTED, "trade happened")
    mock_client.post.assert_called_once()
    call_kwargs = mock_client.post.call_args
    assert "456" in str(call_kwargs)
