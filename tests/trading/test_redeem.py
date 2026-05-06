"""Tests for trading/redeem.py — all mocked, no real web3 calls."""
import pytest
from unittest.mock import MagicMock, patch
from trading.redeem import check_oracle_resolved, CTF_EXCHANGE, PMCT_TOKEN, INDEX_SETS
from trading.redeem_lock import RedeemLock
import trading.redeem_lock as rl


@pytest.fixture(autouse=True)
def reset_redeem_lock():
    rl._held = False
    yield
    rl._held = False


# --- RedeemLock ---

def test_lock_acquire_returns_true_when_free():
    lock = RedeemLock()
    assert lock.acquire() is True


def test_lock_acquire_returns_false_when_held():
    lock = RedeemLock()
    lock.acquire()
    assert lock.acquire() is False


def test_lock_release_allows_reacquire():
    lock = RedeemLock()
    lock.acquire()
    lock.release()
    assert lock.acquire() is True


# --- check_oracle_resolved ---

def test_oracle_resolved_when_numerator_nonzero():
    mock_ctf = MagicMock()
    mock_ctf.functions.payoutNumerators.return_value.call.return_value = 1
    assert check_oracle_resolved(mock_ctf, "0x" + "a" * 64) is True


def test_oracle_not_resolved_when_all_zeros():
    mock_ctf = MagicMock()
    mock_ctf.functions.payoutNumerators.return_value.call.return_value = 0
    assert check_oracle_resolved(mock_ctf, "0x" + "a" * 64) is False


def test_oracle_check_returns_false_on_exception():
    mock_ctf = MagicMock()
    mock_ctf.functions.payoutNumerators.side_effect = Exception("rpc error")
    assert check_oracle_resolved(mock_ctf, "0x" + "a" * 64) is False


# --- constants ---

def test_ctf_exchange_address():
    assert CTF_EXCHANGE == "0xE111180000d2663C0091e4f400237545B87B996B"


def test_index_sets():
    assert INDEX_SETS == [1, 2]
