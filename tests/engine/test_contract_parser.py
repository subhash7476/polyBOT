import pytest
from datetime import datetime, timezone
from engine.contract_parser import parse_contract, ParsedContract, _parse_expiry


# --- Asset detection ---

def test_detects_btc_uppercase():
    c = parse_contract("t1", "Will BTC be above $85,000 by end of March?")
    assert c.asset == "BTC"


def test_detects_bitcoin_full_name():
    c = parse_contract("t1", "Will Bitcoin reach $90k this month?")
    assert c.asset == "BTC"


def test_detects_eth():
    c = parse_contract("t1", "Will ETH be above $4,000 by June?")
    assert c.asset == "ETH"


def test_detects_ethereum_full_name():
    c = parse_contract("t1", "Will Ethereum hit $5000 by end of year?")
    assert c.asset == "ETH"


# --- Direction detection ---

def test_detects_above():
    c = parse_contract("t1", "Will BTC be above $85,000 by end of March?")
    assert c.direction == "above"


def test_detects_over():
    c = parse_contract("t1", "Will BTC go over $90k by April?")
    assert c.direction == "above"


def test_detects_below():
    c = parse_contract("t1", "Will BTC fall below $70,000 by March?")
    assert c.direction == "below"


def test_detects_under():
    c = parse_contract("t1", "Will BTC drop under $75k before June?")
    assert c.direction == "below"


# --- Price extraction ---

def test_price_plain_dollar():
    c = parse_contract("t1", "Will BTC be above $85000 by March?")
    assert c.target_price == 85000.0


def test_price_with_comma():
    c = parse_contract("t1", "Will BTC be above $85,000 by March?")
    assert c.target_price == 85000.0


def test_price_k_suffix():
    c = parse_contract("t1", "Will BTC hit $85k by March?")
    assert c.target_price == 85000.0


def test_price_K_uppercase():
    c = parse_contract("t1", "Will BTC reach $90K by April?")
    assert c.target_price == 90000.0


def test_price_decimal():
    c = parse_contract("t1", "Will ETH be above $3,500.50 by June?")
    assert abs(c.target_price - 3500.5) < 0.01


# --- Expiry extraction ---

def test_expiry_month_name():
    c = parse_contract("t1", "Will BTC be above $85,000 by end of March?")
    assert c.expiry is not None
    assert c.expiry.month == 3


def test_expiry_month_with_day():
    c = parse_contract("t1", "Will BTC hit $90k by June 28?")
    assert c.expiry is not None
    assert c.expiry.month == 6
    assert c.expiry.day == 28


def test_expiry_this_week():
    c = parse_contract("t1", "Will BTC be above $85k this week?")
    assert c.expiry is not None


def test_expiry_defaults_to_eom_when_missing():
    c = parse_contract("t1", "Will BTC be above $85,000?")
    assert c.expiry is not None  # defaults to end of current month


# --- Rate contracts ---

def test_rate_contract_detected():
    c = parse_contract("t1", "Will the Fed cut rates by 25bps at the May FOMC meeting?")
    assert c.category == "rates"
    assert c.parseable is True


def test_fomc_contract_detected():
    c = parse_contract("t1", "FOMC cuts rates in June?")
    assert c.category == "rates"


# --- Unparseable contracts ---

def test_no_asset_generic_binary():
    # Sports markets are now detected before the generic binary catch-all
    c = parse_contract("t1", "Will the Lakers win the championship?")
    assert c.parseable is True
    assert c.category == "sports"


def test_no_direction_not_parseable():
    c = parse_contract("t1", "Will BTC be at $85000 by March?")
    assert c.parseable is False


def test_no_price_not_parseable():
    c = parse_contract("t1", "Will BTC be above its ATH by March?")
    assert c.parseable is False


# --- token_id and question preserved ---

def test_token_id_preserved():
    c = parse_contract("abc123", "Will BTC be above $85k by March?")
    assert c.token_id == "abc123"


def test_question_preserved():
    q = "Will BTC be above $85,000 by end of March?"
    c = parse_contract("t1", q)
    assert c.question == q


# --- Short-dated crypto direction markets ---

def test_short_dated_btc_up_minutes():
    c = parse_contract("t1", "Will BTC go up in the next 5 minutes?")
    assert c.asset == "BTC"
    assert c.direction == "above"
    assert c.category == "crypto"
    assert c.parseable is True
    assert c.T_days is not None
    assert abs(c.T_days - 5 / 1440) < 1e-9


def test_short_dated_eth_decrease_hour():
    c = parse_contract("t1", "Will ETH price decrease in the next hour?")
    assert c.asset == "ETH"
    assert c.direction == "below"
    assert c.category == "crypto"
    assert c.parseable is True
    assert c.T_days is not None
    assert abs(c.T_days - 1 / 24) < 1e-9


def test_short_dated_bitcoin_up_15_minutes():
    c = parse_contract("t1", "Will Bitcoin price go up in the next 15 minutes?")
    assert c.asset == "BTC"
    assert c.direction == "above"
    assert c.parseable is True
    assert abs(c.T_days - 15 / 1440) < 1e-9


def test_short_dated_sol_rise_next_hour():
    c = parse_contract("t1", "Will SOL rise in the next 2 hours?")
    assert c.asset == "SOL"
    assert c.direction == "above"
    assert c.parseable is True
    assert abs(c.T_days - 2 / 24) < 1e-9


def test_short_dated_target_price_is_none():
    c = parse_contract("t1", "Will BTC go up in the next 5 minutes?")
    assert c.target_price is None


# --- Generic binary catch-all ---

def test_generic_binary_un_resolution():
    c = parse_contract("t1", "Will the UN pass a resolution on AI?")
    assert c.category == "event"
    assert c.direction == "yes"
    assert c.parseable is True


def test_generic_binary_spacex_launch():
    c = parse_contract("t1", "Will SpaceX launch Starship next month?")
    assert c.category == "event"
    assert c.direction == "yes"
    assert c.parseable is True


def test_generic_binary_does_not_catch_non_will():
    # "Lakers" is now a sports keyword — matched as sports regardless of "Will"
    c = parse_contract("t1", "Lakers win the championship?")
    assert c.category == "sports"
    assert c.parseable is True


def test_generic_binary_does_not_catch_crypto_with_asset():
    # A crypto question with a known asset should NOT fall through to generic binary
    c = parse_contract("t1", "Will BTC be above its ATH by March?")
    # has asset but no price → parseable=False (not rerouted to event)
    assert c.parseable is False
