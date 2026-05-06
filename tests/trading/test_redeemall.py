"""Tests for trading/redeemall.py — classify_positions + tracked redemption mocked."""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from trading.positions import TrackedPosition
from trading.redeemall import classify_positions, redeem_tracked_positions, run_redeemall
import trading.redeem_lock as rl


@pytest.fixture(autouse=True)
def reset_redeem_lock():
    rl._held = False
    yield
    rl._held = False


def test_classify_all_active():
    positions = [{"market": {"closed": False, "resolved": False}}] * 3
    r, p, a = classify_positions(positions)
    assert len(a) == 3 and len(r) == 0 and len(p) == 0


def test_classify_all_redeemable():
    positions = [{"market": {"closed": True, "resolved": True}}] * 2
    r, p, a = classify_positions(positions)
    assert len(r) == 2 and len(a) == 0 and len(p) == 0


def test_classify_mixed():
    positions = [
        {"market": {"closed": True,  "resolved": True}},
        {"market": {"closed": True,  "resolved": False}},
        {"market": {"closed": False, "resolved": False}},
    ]
    r, p, a = classify_positions(positions)
    assert len(r) == 1 and len(p) == 1 and len(a) == 1


@pytest.mark.asyncio
async def test_run_redeemall_skips_if_lock_held():
    rl._held = True
    result = await run_redeemall("0xWALLET", "http://rpc", "0xKEY")
    assert result == 0


@pytest.mark.asyncio
async def test_run_redeemall_returns_count_of_successful_redemptions():
    positions = [
        {"market": {"closed": True, "resolved": True}, "conditionId": "0x" + "a" * 64},
        {"market": {"closed": True, "resolved": True}, "conditionId": "0x" + "b" * 64},
    ]

    with patch("trading.redeemall.fetch_positions", new=AsyncMock(return_value=positions)), \
         patch("trading.redeemall.redeem_position", return_value=True), \
         patch("trading.redeemall.asyncio.sleep", new=AsyncMock()):
        result = await run_redeemall("0xWALLET", "http://rpc", "0xKEY")

    assert result == 2


@pytest.mark.asyncio
async def test_run_redeemall_counts_only_successful():
    positions = [
        {"market": {"closed": True, "resolved": True}, "conditionId": "0x" + "a" * 64},
        {"market": {"closed": True, "resolved": True}, "conditionId": "0x" + "b" * 64},
    ]
    redeem_results = [True, False]

    with patch("trading.redeemall.fetch_positions", new=AsyncMock(return_value=positions)), \
         patch("trading.redeemall.redeem_position", side_effect=redeem_results), \
         patch("trading.redeemall.asyncio.sleep", new=AsyncMock()):
        result = await run_redeemall("0xWALLET", "http://rpc", "0xKEY")

    assert result == 1


def test_extract_and_redeem_tracked_positions_sync():
    tracked = {
        "tok1": TrackedPosition(
            token_id="tok1",
            no_token_id="tok1_no",
            question="q1",
            category="crypto",
            group_key="btc_above",
            side="BUY_YES",
            size_usdc=50.0,
            entry_price=0.5,
            market_price_at_open=0.5,
            condition_id="0x" + "a" * 64,
            status="resolved_pending_redeem",
        )
    }
    positions = [
        {
            "asset": "tok1",
            "conditionId": "0x" + "a" * 64,
            "market": {"closed": True, "resolved": True},
        }
    ]

    async def _run():
        with patch("trading.redeemall.fetch_positions", new=AsyncMock(return_value=positions)), \
             patch("trading.redeemall.redeem_position", return_value=True), \
             patch("trading.redeemall.asyncio.sleep", new=AsyncMock()):
            return await redeem_tracked_positions("0xWALLET", "http://rpc", "0xKEY", tracked)

    redeemed = asyncio.run(_run())
    assert redeemed == ["tok1"]
