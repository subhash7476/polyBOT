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


def test_election_parseable_has_expiry():
    c = parse_contract("tok1", "Will Republicans win the 2026 midterm?")
    assert c.expiry is not None
