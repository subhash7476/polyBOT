"""Tests for Phase 4 on-chain data: DeFiLlama + Blockchain.com endpoints."""
import pytest
from market.state import FeedState
from feeds.onchain import (
    OnChainFeed,
    _compute_stablecoin_signal,
    _compute_hashrate_signal,
    DEFI_LLAMA_STABLES,
    BLOCKCHAIN_COM_BASE,
)


def test_defi_llama_url_defined():
    assert "llama" in DEFI_LLAMA_STABLES.lower()


def test_blockchain_com_url_defined():
    assert "blockchain" in BLOCKCHAIN_COM_BASE.lower()


def test_stablecoin_signal_rising_is_bullish():
    """Rising stablecoin supply = new money entering = positive signal."""
    signal = _compute_stablecoin_signal(current=200e9, prev=180e9)
    assert signal > 0  # bullish


def test_stablecoin_signal_falling_is_bearish():
    signal = _compute_stablecoin_signal(current=170e9, prev=200e9)
    assert signal < 0  # bearish


def test_stablecoin_signal_clamped():
    """Signal is clamped to [-1, 1]."""
    signal = _compute_stablecoin_signal(current=1e18, prev=1.0)  # extreme
    assert -1.0 <= signal <= 1.0


def test_stablecoin_signal_flat_is_neutral():
    signal = _compute_stablecoin_signal(current=200e9, prev=200e9)
    assert signal == 0.0


def test_hashrate_signal_rising_is_bullish():
    """Rising hash rate = miner confidence = bullish."""
    signal = _compute_hashrate_signal(current=800e18, month_ago=600e18)
    assert signal > 0


def test_hashrate_signal_falling_is_bearish():
    signal = _compute_hashrate_signal(current=400e18, month_ago=600e18)
    assert signal < 0


def test_hashrate_signal_clamped():
    signal = _compute_hashrate_signal(current=1e30, month_ago=1.0)
    assert -1.0 <= signal <= 1.0


def test_feedstate_has_new_onchain_fields():
    fs = FeedState()
    assert hasattr(fs, "stablecoin_supply_change")
    assert hasattr(fs, "btc_hashrate_trend")
    assert fs.stablecoin_supply_change is None
    assert fs.btc_hashrate_trend is None
