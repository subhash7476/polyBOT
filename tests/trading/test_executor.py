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
