"""Tests for trading/clob_factory.py — no real SDK or network calls."""
import pytest


def test_pmct_token_is_valid_address():
    from trading.clob_factory import PMCT_TOKEN
    assert PMCT_TOKEN.startswith("0x")
    assert len(PMCT_TOKEN) == 42


def test_ctf_exchange_v2_is_valid_address():
    from trading.clob_factory import CTF_EXCHANGE_V2
    assert CTF_EXCHANGE_V2.startswith("0x")
    assert len(CTF_EXCHANGE_V2) == 42


def test_neg_risk_exchange_v2_is_valid_address():
    from trading.clob_factory import NEG_RISK_EXCHANGE_V2
    assert NEG_RISK_EXCHANGE_V2.startswith("0x")
    assert len(NEG_RISK_EXCHANGE_V2) == 42


def test_pmct_differs_from_old_usdc_bridged():
    from trading.clob_factory import PMCT_TOKEN
    OLD_USDC_BRIDGED = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
    assert PMCT_TOKEN != OLD_USDC_BRIDGED


def test_v2_exchange_differs_from_v1():
    from trading.clob_factory import CTF_EXCHANGE_V2
    OLD_CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
    assert CTF_EXCHANGE_V2 != OLD_CTF_EXCHANGE


def test_import_guard_raises_clear_error(monkeypatch):
    """If SDK not installed, ImportError has actionable message."""
    import builtins
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name.startswith("py_clob_client_v2"):
            raise ImportError("No module named 'py_clob_client_v2'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)

    from trading import clob_factory
    import importlib
    importlib.reload(clob_factory)

    with pytest.raises(ImportError, match="pip install py-clob-client-v2"):
        clob_factory._import_sdk()
