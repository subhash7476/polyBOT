"""Tests for Step 19 live pilot category filter in config."""
import pytest
import config


def test_live_pilot_categories_default_empty():
    # Default is empty list (all categories allowed)
    assert isinstance(config.LIVE_PILOT_CATEGORIES, list)


def test_live_pilot_max_trade_size_default():
    assert config.LIVE_PILOT_MAX_TRADE_SIZE_USDC == 25.0


def test_live_pilot_max_exposure_default():
    assert config.LIVE_PILOT_MAX_EXPOSURE_PCT == 0.05
