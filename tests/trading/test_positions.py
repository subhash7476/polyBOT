import asyncio
import json

from engine.contract_parser import ParsedContract
from trading.positions import (
    CLOSED_PAPER,
    OPEN,
    REDEEMED,
    RESOLVED_PENDING_REDEEM,
    PositionLedger,
    TrackedPosition,
)
from trading.risk import RiskManager


def make_parsed() -> ParsedContract:
    return ParsedContract(
        token_id="tok1",
        question="Will BTC be above $90k by Mar 31?",
        asset="BTC",
        direction="above",
        target_price=90000.0,
        category="crypto",
    )


def test_position_ledger_loads_latest_active_states(tmp_path):
    ledger = PositionLedger(str(tmp_path / "positions.jsonl"))
    open_pos = TrackedPosition(
        token_id="tok1",
        no_token_id="tok1_no",
        question="q1",
        category="crypto",
        group_key="btc_above",
        side="BUY_YES",
        size_usdc=50.0,
        entry_price=0.5,
        market_price_at_open=0.5,
        status=OPEN,
    )
    redeemed_pos = TrackedPosition(
        token_id="tok2",
        no_token_id="tok2_no",
        question="q2",
        category="crypto",
        group_key="btc_above",
        side="BUY_YES",
        size_usdc=50.0,
        entry_price=0.5,
        market_price_at_open=0.5,
        status=RESOLVED_PENDING_REDEEM,
    )
    ledger.append(open_pos)
    ledger.append(redeemed_pos)
    redeemed_pos.status = REDEEMED
    ledger.append(redeemed_pos)

    open_positions, pending = ledger.load_active()
    assert list(open_positions) == ["tok1"]
    assert pending == {}


def test_risk_manager_restores_positions_from_ledger(tmp_path):
    ledger = PositionLedger(str(tmp_path / "positions.jsonl"))
    restored = TrackedPosition(
        token_id="tok1",
        no_token_id="tok1_no",
        question="q1",
        category="crypto",
        group_key="btc_above",
        side="BUY_YES",
        size_usdc=50.0,
        entry_price=0.5,
        market_price_at_open=0.48,
        status=OPEN,
    )
    ledger.append(restored)

    rm = RiskManager(bankroll=1000.0, ledger=ledger)
    assert "tok1" in rm.open_positions
    assert rm.open_positions["tok1"].question == "q1"


def test_resolve_position_closes_paper_trade_and_records_terminal_state(tmp_path):
    ledger = PositionLedger(str(tmp_path / "positions.jsonl"))
    rm = RiskManager(bankroll=1000.0, ledger=ledger)

    async def _run():
        await rm.open_position(
            "tok1",
            make_parsed(),
            50.0,
            0.5,
            side="BUY_YES",
            no_token_id="tok1_no",
            question="q1",
        )
        pos = await rm.resolve_position("tok1", True, paper=True, resolved_at=123.0)
        return pos

    pos = asyncio.run(_run())
    assert pos is not None
    assert pos.status == CLOSED_PAPER
    assert rm.daily_pnl == 50.0
    assert "tok1" not in rm.open_positions
    latest = ledger.load_latest()["tok1"]
    assert latest.status == CLOSED_PAPER
    assert latest.resolved_yes is True


def test_resolve_then_redeem_live_trade_updates_ledger(tmp_path):
    ledger = PositionLedger(str(tmp_path / "positions.jsonl"))
    rm = RiskManager(bankroll=1000.0, ledger=ledger)

    async def _run():
        await rm.open_position(
            "tok1",
            make_parsed(),
            50.0,
            0.5,
            side="BUY_NO",
            no_token_id="tok1_no",
            question="q1",
        )
        await rm.resolve_position("tok1", False, paper=False, resolved_at=123.0)
        return await rm.mark_redeemed("tok1", redeemed_at=456.0)

    pos = asyncio.run(_run())
    assert pos is not None
    assert pos.status == REDEEMED
    assert pos.redeemed_at == 456.0
    latest = ledger.load_latest()["tok1"]
    assert latest.status == REDEEMED
    assert latest.resolved_yes is False


def test_update_wallet_metadata_persists_condition_id(tmp_path):
    ledger = PositionLedger(str(tmp_path / "positions.jsonl"))
    rm = RiskManager(bankroll=1000.0, ledger=ledger)

    async def _run():
        await rm.open_position(
            "tok1",
            make_parsed(),
            50.0,
            0.5,
            side="BUY_YES",
            no_token_id="tok1_no",
            question="q1",
        )
        return await rm.update_wallet_metadata("tok1", condition_id="0x" + "a" * 64)

    pos = asyncio.run(_run())
    assert pos is not None
    assert pos.condition_id == "0x" + "a" * 64
    latest = ledger.load_latest()["tok1"]
    assert latest.condition_id == "0x" + "a" * 64
