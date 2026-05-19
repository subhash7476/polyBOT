from engine.contract_parser import parse_contract


def test_election_category_detected():
    c = parse_contract("tok1", "Will Democrats win the 2026 midterm elections?")
    assert c.category == "election"
    assert c.parseable is True


def test_election_win_direction():
    c = parse_contract("tok1", "Will Biden win the 2026 Senate race?")
    assert c.category == "election"
    assert c.direction == "yes"


def test_event_deadline_category():
    c = parse_contract("tok1", "Will the US debt ceiling be raised by March 31?")
    assert c.category == "event"
    assert c.parseable is True


def test_event_crypto_milestone():
    c = parse_contract("tok1", "Will an Ethereum ETF be approved by Q2 2026?")
    assert c.category == "event"
    assert c.parseable is True


def test_existing_crypto_unchanged():
    c = parse_contract("tok1", "Will BTC reach $100k by March?")
    assert c.category == "crypto"


def test_existing_rates_unchanged():
    c = parse_contract("tok1", "Will the Fed cut rates in May?")
    assert c.category == "rates"


def test_global_temperature_increase_is_weather():
    c = parse_contract(
        "tok1",
        "Will global temperature increase by between 1.10°C and 1.14°C in May 2026?",
    )
    assert c.category == "weather"
    assert c.parseable is True
    assert c.asset == "global-temperature"
    assert c.direction == "bucket"


def test_election_parseable_has_expiry():
    c = parse_contract("tok1", "Will Republicans win the 2026 midterm?")
    assert c.expiry is not None


def test_spy_price_market_is_finance():
    c = parse_contract("tok1", "Will SPY close above $500 by May 31, 2026?")
    assert c.category == "finance"
    assert c.asset == "SPY"
    assert c.direction == "above"
    assert c.target_price == 500.0
