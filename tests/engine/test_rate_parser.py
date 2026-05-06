from engine.contract_parser import parse_contract


def test_fed_cut_25bps():
    c = parse_contract("fed1", "Will the Fed cut rates by 25 basis points in May?")
    assert c.parseable
    assert c.category == "rates"
    assert c.target_price == 25.0
    assert c.direction == "below"  # cut = rate goes below
    assert c.expiry is not None


def test_fed_hike_50bps():
    c = parse_contract("fed2", "Will the FOMC hike rates by 50bps in June?")
    assert c.parseable
    assert c.category == "rates"
    assert c.target_price == 50.0
    assert c.direction == "above"  # hike = rate goes above


def test_fed_hold_rates():
    c = parse_contract("fed3", "Will the Fed hold rates steady in May?")
    assert c.parseable
    assert c.category == "rates"
    assert c.direction == "hold"


def test_fed_rate_with_percentage():
    c = parse_contract("fed4", "Will the Fed funds rate be above 4.5% by June?")
    assert c.parseable
    assert c.category == "rates"
    assert c.target_price == 4.5


def test_fomc_meeting_cut():
    c = parse_contract("fed5", "Will the FOMC cut rates at the March meeting?")
    assert c.parseable
    assert c.category == "rates"
    assert c.direction == "below"


def test_rate_contract_without_direction_not_parseable():
    # No cut/hike/hold keyword — can't infer direction
    c = parse_contract("fed6", "Will the Fed change rates in May?")
    assert c.category == "rates"
    assert not c.parseable
