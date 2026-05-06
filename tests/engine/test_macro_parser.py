from engine.contract_parser import parse_contract


def test_parse_cpi_above():
    c = parse_contract("m1", "Will CPI be above 3.5% in March?")
    assert c.parseable
    assert c.category == "macro"
    assert c.asset == "CPI"
    assert c.direction == "above"
    assert c.target_price == 3.5


def test_parse_inflation_alias():
    c = parse_contract("m2", "Will US inflation exceed 3% by April?")
    assert c.parseable
    assert c.category == "macro"
    assert c.asset == "CPI"


def test_parse_unemployment():
    c = parse_contract("m3", "Will unemployment be above 4.5% in May?")
    assert c.parseable
    assert c.category == "macro"
    assert c.asset == "UNEMPLOYMENT"
    assert c.target_price == 4.5


def test_parse_nfp():
    c = parse_contract("m4", "Will nonfarm payrolls exceed 200,000 in March?")
    assert c.parseable
    assert c.category == "macro"
    assert c.asset == "NFP"


def test_parse_gdp():
    c = parse_contract("m5", "Will GDP growth be above 2% in Q1?")
    assert c.parseable
    assert c.category == "macro"
    assert c.asset == "GDP"
    assert c.target_price == 2.0


def test_macro_without_direction_not_parseable():
    c = parse_contract("m6", "Will CPI change in March?")
    assert c.category == "macro"
    assert not c.parseable


def test_macro_detected_before_crypto_asset():
    """'inflation' should be macro, not confused with crypto."""
    c = parse_contract("m7", "Will US CPI be above 3.5% in April?")
    assert c.category == "macro"
    assert c.asset != "BTC"
