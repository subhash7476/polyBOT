from datetime import datetime, timezone, timedelta
from market.state import ContractState


def test_contract_state_vpin_defaults():
    cs = ContractState(yes_token_id="a", no_token_id="b", question="q", category="crypto")
    assert cs.vpin == 0.5
    assert cs.vpin_updated_at == 0.0


def test_hours_to_resolution_no_date():
    cs = ContractState(yes_token_id="a", no_token_id="b", question="q", category="crypto")
    assert cs.hours_to_resolution == 48.0


def test_hours_to_resolution_future():
    future = (datetime.now(timezone.utc) + timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cs = ContractState(yes_token_id="a", no_token_id="b", question="q", category="crypto",
                       end_date_iso=future)
    assert 11.5 < cs.hours_to_resolution < 12.5


def test_hours_to_resolution_past_clamps_to_zero():
    past = "2020-01-01T00:00:00Z"
    cs = ContractState(yes_token_id="a", no_token_id="b", question="q", category="crypto",
                       end_date_iso=past)
    assert cs.hours_to_resolution == 0.0


def test_hours_to_resolution_malformed_date_returns_default():
    cs = ContractState(yes_token_id="a", no_token_id="b", question="q", category="crypto",
                       end_date_iso="not-a-date")
    assert cs.hours_to_resolution == 48.0


def test_hours_to_resolution_caps_at_168():
    far_future = (datetime.now(timezone.utc) + timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cs = ContractState(yes_token_id="a", no_token_id="b", question="q", category="crypto",
                       end_date_iso=far_future)
    assert cs.hours_to_resolution == 168.0
