import json
import time
from pathlib import Path
from maker.fill_ledger import FillLedger


def test_append_creates_file(tmp_path):
    ledger = FillLedger(base_dir=tmp_path)
    ledger.append(
        session_id="s1",
        token_id="tok123",
        question="Will X happen?",
        side="BUY",
        price=0.25,
        size=10.0,
        cash_flow=-2.5,
        realized_pnl=0.0,
        inventory_after=10.0,
        filled_at=1_000_000.0,
    )
    files = list((tmp_path / "maker_fills").glob("*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text().strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["token_id"] == "tok123"
    assert record["side"] == "BUY"
    assert record["price"] == 0.25
    assert record["size"] == 10.0
    assert record["cash_flow"] == -2.5
    assert record["realized_pnl"] == 0.0
    assert record["inventory_after"] == 10.0
    assert record["session_id"] == "s1"
    assert "fill_id" in record
    assert "ts" in record


def test_fill_id_is_unique(tmp_path):
    ledger = FillLedger(base_dir=tmp_path)
    ids = set()
    for i in range(5):
        ledger.append(
            session_id="s1", token_id="tok1", question="Q",
            side="BUY", price=0.25, size=10.0,
            cash_flow=-2.5, realized_pnl=0.0, inventory_after=float(i),
            filled_at=1_000_000.0 + i,
        )
    files = list((tmp_path / "maker_fills").glob("*.jsonl"))
    for line in files[0].read_text().strip().splitlines():
        r = json.loads(line)
        ids.add(r["fill_id"])
    assert len(ids) == 5


def test_filename_uses_utc_date(tmp_path):
    ledger = FillLedger(base_dir=tmp_path)
    ts = 1_000_000.0  # 1970-01-12 in UTC
    ledger.append(
        session_id="s1", token_id="tok1", question="Q",
        side="BUY", price=0.5, size=1.0,
        cash_flow=-0.5, realized_pnl=0.0, inventory_after=1.0,
        filled_at=ts,
    )
    files = list((tmp_path / "maker_fills").glob("*.jsonl"))
    assert len(files) == 1
    from datetime import datetime, timezone
    expected_date = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    assert expected_date in files[0].name


def test_multiple_appends_same_file(tmp_path):
    ledger = FillLedger(base_dir=tmp_path)
    for i in range(3):
        ledger.append(
            session_id="s1", token_id="tok1", question="Q",
            side="BUY", price=0.25, size=10.0,
            cash_flow=-2.5, realized_pnl=0.0, inventory_after=float(i + 1) * 10,
            filled_at=1_000_000.0 + i,
        )
    files = list((tmp_path / "maker_fills").glob("*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text().strip().splitlines()
    assert len(lines) == 3


def test_read_today_returns_records(tmp_path):
    ledger = FillLedger(base_dir=tmp_path)
    ledger.append(
        session_id="s1", token_id="tok1", question="Q",
        side="BUY", price=0.25, size=10.0,
        cash_flow=-2.5, realized_pnl=0.0, inventory_after=10.0,
        filled_at=time.time(),
    )
    records = ledger.read_date()  # today by default
    assert len(records) == 1
    assert records[0]["token_id"] == "tok1"
