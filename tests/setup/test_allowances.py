"""Tests for setup/allowances.py — all using mocked web3, no real network calls."""
from unittest.mock import MagicMock
from setup.allowances import build_approve_tx, SPENDERS, MAX_UINT256, PMCT_TOKEN


def test_spenders_has_four_entries():
    assert len(SPENDERS) == 4


def test_spender_names():
    names = [s[0] for s in SPENDERS]
    assert "CTF Exchange V2" in names
    assert "NegRisk CTF Exchange V2" in names
    assert "NegRisk Adapter" in names
    assert "Collateral Onramp" in names


def test_max_uint256():
    assert MAX_UINT256 == 2**256 - 1


def test_pmct_token_address():
    assert PMCT_TOKEN == "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"


def test_build_approve_tx_returns_dict():
    mock_contract = MagicMock()
    mock_contract.functions.approve.return_value.build_transaction.return_value = {
        "to":   "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
        "data": "0xapprovedata",
        "gas":  100_000,
    }
    spender = SPENDERS[0][1]
    tx = build_approve_tx(mock_contract, spender=spender, account_address="0x" + "a" * 40, chain_id=137)
    assert isinstance(tx, dict)
    assert tx["gas"] == 100_000


def test_build_approve_tx_calls_approve_with_max_uint256():
    mock_contract = MagicMock()
    mock_contract.functions.approve.return_value.build_transaction.return_value = {}
    spender = SPENDERS[1][1]
    build_approve_tx(mock_contract, spender=spender, account_address="0x" + "b" * 40, chain_id=137)
    # Check that approve() was called with the spender and MAX_UINT256
    approve_call = mock_contract.functions.approve.call_args
    assert approve_call is not None
    args = approve_call[0]  # positional args
    assert args[1] == MAX_UINT256
