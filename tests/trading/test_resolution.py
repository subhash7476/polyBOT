import asyncio
import json

from calibration.tracker import CalibrationTracker
from engine.contract_parser import ParsedContract
from trading.positions import CLOSED_PAPER, PositionLedger, RESOLVED_PENDING_REDEEM
from trading.resolution import (
    _resolved_yes_from_market_payload,
    process_resolutions,
    reconcile_wallet_positions,
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


def test_resolved_yes_from_explicit_winner():
    assert _resolved_yes_from_market_payload({"winningOutcome": "Yes"}) is True
    assert _resolved_yes_from_market_payload({"market": {"winner": "No"}}) is False


def test_resolved_yes_from_outcome_prices():
    assert _resolved_yes_from_market_payload({"outcomePrices": [1, 0]}) is True
    assert _resolved_yes_from_market_payload({"market": {"outcomePrices": [0, 1]}}) is False


def test_process_resolutions_settles_paper_trade(tmp_path, monkeypatch):
    ledger = PositionLedger(str(tmp_path / "positions.jsonl"))
    tracker = CalibrationTracker(str(tmp_path / "fills.jsonl"))
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
        tracker.log_signal("tok1", 0.7, 0.5, {}, 50.0, 0.1)

        async def fake_fetch_market_resolution(client, token_id):
            return True, True, {"resolved": True, "winningOutcome": "Yes"}

        monkeypatch.setattr("trading.resolution.fetch_market_resolution", fake_fetch_market_resolution)
        return await process_resolutions(rm, tracker, paper=True)

    stats = asyncio.run(_run())
    assert stats["resolved"] == 1
    latest = ledger.load_latest()["tok1"]
    assert latest.status == CLOSED_PAPER
    record = json.loads((tmp_path / "fills.jsonl").read_text(encoding="utf-8").strip())
    assert record["outcome"] == 1


def test_process_resolutions_moves_live_trade_to_pending_redeem(tmp_path, monkeypatch):
    ledger = PositionLedger(str(tmp_path / "positions.jsonl"))
    tracker = CalibrationTracker(str(tmp_path / "fills.jsonl"))
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
        tracker.log_signal("tok1", 0.3, 0.5, {}, 50.0, 0.1, side="BUY_NO")

        async def fake_fetch_market_resolution(client, token_id):
            return True, False, {"resolved": True, "winningOutcome": "No"}

        async def fake_fetch_positions(wallet):
            return [{"asset": "tok1"}]

        monkeypatch.setattr("trading.resolution.fetch_market_resolution", fake_fetch_market_resolution)
        monkeypatch.setattr("trading.resolution.fetch_positions", fake_fetch_positions)
        return await process_resolutions(rm, tracker, paper=False, wallet_address="0xabc")

    stats = asyncio.run(_run())
    assert stats["resolved"] == 1
    latest = ledger.load_latest()["tok1"]
    assert latest.status == RESOLVED_PENDING_REDEEM
    assert "tok1" in rm.pending_redemptions


def test_reconcile_wallet_positions_reports_missing(tmp_path, monkeypatch):
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

        async def fake_fetch_positions(wallet):
            return []

        monkeypatch.setattr("trading.resolution.fetch_positions", fake_fetch_positions)
        return await reconcile_wallet_positions("0xabc", rm)

    mismatches = asyncio.run(_run())
    assert mismatches == ["tok1"]
