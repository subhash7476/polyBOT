import pytest
from feeds.deribit import parse_dvol_message, parse_options_chain, _compute_skew


def make_dvol_msg(channel: str, volatility: float, index_price: float) -> dict:
    return {
        "method": "subscription",
        "params": {
            "channel": channel,
            "data": {"volatility": volatility, "index_price": index_price},
        },
    }


# --- DVOL parsing ---

def test_parse_dvol_btc():
    msg = make_dvol_msg("deribit_volatility_index.btc_usd", 72.4, 84200.0)
    result = parse_dvol_message(msg)
    assert result == {"btc_dvol": 72.4, "btc_price": 84200.0}


def test_parse_dvol_eth():
    msg = make_dvol_msg("deribit_volatility_index.eth_usd", 68.1, 3200.0)
    result = parse_dvol_message(msg)
    assert result == {"eth_dvol": 68.1, "eth_price": 3200.0}


def test_parse_dvol_unknown_channel_returns_none():
    msg = make_dvol_msg("unknown.channel", 50.0, 100.0)
    assert parse_dvol_message(msg) is None


def test_parse_dvol_non_subscription_returns_none():
    msg = {"method": "heartbeat", "params": {}}
    assert parse_dvol_message(msg) is None


def test_daily_move_formula():
    # dvol / 20 = expected daily move percent
    assert abs(80.0 / 20 - 4.0) < 1e-9
    assert abs(20.0 / 20 - 1.0) < 1e-9


# --- Skew computation ---

def test_compute_skew_normal():
    calls = [{"iv": 60.0, "delta": 0.25, "name": "BTC-28MAR26-90000-C"},
             {"iv": 55.0, "delta": 0.50, "name": "BTC-28MAR26-85000-C"}]
    puts  = [{"iv": 65.0, "delta": -0.25, "name": "BTC-28MAR26-80000-P"},
             {"iv": 70.0, "delta": -0.50, "name": "BTC-28MAR26-75000-P"}]
    skew = _compute_skew(calls, puts, target_delta=0.25)
    assert abs(skew - 5.0) < 1e-6   # put IV 65 - call IV 60 = 5


def test_compute_skew_empty_returns_zero():
    assert _compute_skew([], [], target_delta=0.25) == 0.0


def test_compute_skew_no_puts_returns_zero():
    calls = [{"iv": 60.0, "delta": 0.25, "name": "C"}]
    assert _compute_skew(calls, [], target_delta=0.25) == 0.0


# --- Options chain parsing ---

def test_parse_options_chain_separates_calls_puts():
    instruments = [
        {"instrument_name": "BTC-28MAR26-90000-C", "iv": 60.0, "delta": 0.25},
        {"instrument_name": "BTC-28MAR26-80000-P", "iv": 65.0, "delta": -0.25},
        {"instrument_name": "BTC-28MAR26-85000-C", "iv": 0.0,  "delta": 0.50},  # iv=0 filtered
    ]
    result = parse_options_chain(instruments)
    assert result["call_count"] == 1   # iv=0 filtered out
    assert result["put_count"] == 1
    assert result["skew"] == pytest.approx(5.0, abs=1e-4)


def test_parse_options_chain_empty_returns_defaults():
    result = parse_options_chain([])
    assert result["skew"] == 0.0
    assert result["call_count"] == 0
