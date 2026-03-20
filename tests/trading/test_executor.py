import pytest
from trading.executor import CLOBExecutor, OrderResult


def test_order_result_success():
    r = OrderResult(order_id="123", status="MATCHED", filled_price=0.52, filled_size=50.0)
    assert r.success is True


def test_order_result_failure():
    r = OrderResult(order_id=None, status="ERROR", filled_price=0.0, filled_size=0.0, error="timeout")
    assert r.success is False


@pytest.mark.asyncio
async def test_paper_mode_buy_yes_does_not_call_api():
    executor = CLOBExecutor(private_key="0x" + "a" * 64, paper=True)
    result = await executor.place_order(
        yes_token_id="yes123", no_token_id="no123",
        side="BUY_YES", size=50.0, price=0.52
    )
    assert result.success is True
    assert result.order_id == "PAPER"


@pytest.mark.asyncio
async def test_paper_mode_buy_no_uses_no_token():
    executor = CLOBExecutor(private_key="0x" + "a" * 64, paper=True)
    result = await executor.place_order(
        yes_token_id="yes123", no_token_id="no123",
        side="BUY_NO", size=50.0, price=0.48
    )
    assert result.success is True
    assert result.token_used == "no123"


@pytest.mark.asyncio
async def test_paper_mode_buy_yes_uses_yes_token():
    executor = CLOBExecutor(private_key="0x" + "a" * 64, paper=True)
    result = await executor.place_order(
        yes_token_id="yes123", no_token_id="no123",
        side="BUY_YES", size=50.0, price=0.52
    )
    assert result.token_used == "yes123"


@pytest.mark.asyncio
async def test_invalid_side_raises():
    executor = CLOBExecutor(private_key="0x" + "a" * 64, paper=True)
    with pytest.raises(ValueError, match="side must be BUY_YES or BUY_NO"):
        await executor.place_order("yes", "no", side="BUY", size=10.0, price=0.5)


@pytest.mark.asyncio
async def test_invalid_size_raises():
    executor = CLOBExecutor(private_key="0x" + "a" * 64, paper=True)
    with pytest.raises(ValueError, match="size must be > 0"):
        await executor.place_order("yes", "no", side="BUY_YES", size=0.0, price=0.5)


@pytest.mark.asyncio
async def test_cancel_paper_returns_true():
    executor = CLOBExecutor(private_key="0x" + "a" * 64, paper=True)
    assert await executor.cancel_order("PAPER_123") is True


def test_executor_wallet_address_type0(monkeypatch):
    """SIGNATURE_TYPE=0: address derived from private key."""
    from eth_account import Account
    test_key = "0x" + "a" * 64
    monkeypatch.setenv("SIGNATURE_TYPE", "0")
    monkeypatch.setenv("POLY_PRIVATE_KEY", test_key)
    monkeypatch.setenv("FUNDER_ADDRESS", "")
    import importlib
    import config
    importlib.reload(config)
    import trading.executor
    importlib.reload(trading.executor)
    from trading.executor import CLOBExecutor
    ex = CLOBExecutor(private_key=test_key, paper=True)
    expected = Account.from_key(test_key).address
    assert ex.wallet_address == expected


def test_executor_wallet_address_type1(monkeypatch):
    """SIGNATURE_TYPE=1: address comes from FUNDER_ADDRESS."""
    monkeypatch.setenv("SIGNATURE_TYPE", "1")
    monkeypatch.setenv("FUNDER_ADDRESS", "0xFUNDER123")
    monkeypatch.setenv("POLY_PRIVATE_KEY", "0x" + "b" * 64)
    import importlib
    import config
    importlib.reload(config)
    import trading.executor
    importlib.reload(trading.executor)
    from trading.executor import CLOBExecutor
    ex = CLOBExecutor(private_key="0x" + "b" * 64, paper=True)
    assert ex.wallet_address == "0xFUNDER123"


import asyncio as _asyncio
from unittest.mock import patch as _patch


def test_place_order_retries_on_transient_failure():
    from trading.executor import CLOBExecutor
    ex = CLOBExecutor(private_key="0x" + "a" * 64, paper=False)
    call_count = 0

    class FakeClob:
        def create_order(self, **kw): return None
        def create_and_post_order(self, order):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("transient")
            return {"orderID": "ok123", "status": "live", "price": "0.6", "size": "10"}

    ex._clob = FakeClob()

    async def run():
        with _patch("trading.executor.asyncio.sleep"):
            return await ex.place_order("tok_yes", "tok_no", "BUY_YES", 10.0, 0.6)

    result = _asyncio.run(run())
    assert result.success
    assert call_count == 3


def test_place_order_returns_error_after_all_retries_exhausted():
    from trading.executor import CLOBExecutor
    ex = CLOBExecutor(private_key="0x" + "a" * 64, paper=False)

    class AlwaysFails:
        def create_order(self, **kw): return None
        def create_and_post_order(self, order): raise Exception("always fails")

    ex._clob = AlwaysFails()

    async def run():
        with _patch("trading.executor.asyncio.sleep"):
            return await ex.place_order("tok_yes", "tok_no", "BUY_YES", 10.0, 0.6)

    result = _asyncio.run(run())
    assert not result.success
    assert result.status == "ERROR"
    assert "always fails" in result.error
