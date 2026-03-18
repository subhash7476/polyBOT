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

def test_no_asset_not_parseable():
    c = parse_contract("t1", "Will the Lakers win the championship?")
    assert c.parseable is False


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
