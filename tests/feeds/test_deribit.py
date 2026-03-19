import pytest
from feeds.deribit import parse_dvol_message, parse_options_chain


def make_dvol_msg(channel: str, volatility: float) -> dict:
    return {
        "method": "subscription",
        "params": {
            "channel": channel,
            "data": {"volatility": volatility, "index_name": channel.split(".")[1]},
        },
    }


# --- DVOL parsing ---

def test_parse_dvol_btc():
    msg = make_dvol_msg("deribit_volatility_index.btc_usd", 72.4)
    result = parse_dvol_message(msg)
    assert result == {"asset": "BTC", "dvol": 72.4}


def test_parse_dvol_eth():
    msg = make_dvol_msg("deribit_volatility_index.eth_usd", 68.1)
    result = parse_dvol_message(msg)
    assert result == {"asset": "ETH", "dvol": 68.1}


def test_parse_dvol_sol():
    msg = make_dvol_msg("deribit_volatility_index.sol_usd", 90.5)
    result = parse_dvol_message(msg)
    assert result == {"asset": "SOL", "dvol": 90.5}


def test_parse_dvol_no_index_price():
    """Deribit DVOL channel does not send index_price — confirmed from live data."""
    msg = make_dvol_msg("deribit_volatility_index.btc_usd", 51.0)
    result = parse_dvol_message(msg)
    assert "spot" not in result
    assert result["asset"] == "BTC"


def test_parse_dvol_unknown_channel_returns_none():
    msg = make_dvol_msg("unknown.channel", 50.0)
    assert parse_dvol_message(msg) is None


def test_parse_dvol_non_subscription_returns_none():
    msg = {"method": "heartbeat", "params": {}}
    assert parse_dvol_message(msg) is None


# --- Options chain parsing ---
# Deribit sends: {timestamp, iv, instrument_name, mark_price} — NO delta field.
# Near-OTM filter: mark_price in [0.005, 0.08] BTC.

def test_parse_options_chain_near_otm_filter():
    instruments = [
        {"instrument_name": "BTC-28MAR26-90000-C", "iv": 0.60, "mark_price": 0.02},   # near OTM call — INCLUDED
        {"instrument_name": "BTC-28MAR26-80000-P", "iv": 0.65, "mark_price": 0.03},   # near OTM put  — INCLUDED
        {"instrument_name": "BTC-28MAR26-120000-C", "iv": 1.30, "mark_price": 0.001}, # deep OTM — filtered
        {"instrument_name": "BTC-28MAR26-50000-P", "iv": 0.80, "mark_price": 0.60},   # deep ITM — filtered
        {"instrument_name": "BTC-28MAR26-85000-C", "iv": 0.0,  "mark_price": 0.04},   # iv=0 — filtered
    ]
    result = parse_options_chain(instruments)
    assert result["call_count"] == 1
    assert result["put_count"] == 1
    assert result["skew"] == pytest.approx(0.05, abs=1e-4)  # 0.65 - 0.60 = 0.05


def test_parse_options_chain_positive_skew_put_bid_up():
    """More put buying than call buying → puts more expensive → positive skew."""
    instruments = [
        {"instrument_name": "BTC-28MAR26-90000-C", "iv": 0.50, "mark_price": 0.02},
        {"instrument_name": "BTC-28MAR26-80000-P", "iv": 0.70, "mark_price": 0.03},
    ]
    result = parse_options_chain(instruments)
    assert result["skew"] > 0


def test_parse_options_chain_empty_returns_defaults():
    result = parse_options_chain([])
    assert result["skew"] == 0.0
    assert result["call_count"] == 0
    assert result["put_count"] == 0


def test_parse_options_chain_no_puts_returns_zero_skew():
    instruments = [
        {"instrument_name": "BTC-28MAR26-90000-C", "iv": 0.60, "mark_price": 0.02},
    ]
    result = parse_options_chain(instruments)
    assert result["skew"] == 0.0


def test_parse_options_chain_filters_wildly_illiquid():
    """iv > 5 is filtered as wildly illiquid."""
    instruments = [
        {"instrument_name": "BTC-28MAR26-90000-C", "iv": 10.0, "mark_price": 0.02},
        {"instrument_name": "BTC-28MAR26-80000-P", "iv": 0.65, "mark_price": 0.03},
    ]
    result = parse_options_chain(instruments)
    assert result["call_count"] == 0  # iv=10 filtered
    assert result["put_count"] == 1
