from engine.contract_parser import parse_contract


def test_parse_sol_above():
    c = parse_contract("tok1", "Will SOL be above $200 by end of April?")
    assert c.parseable
    assert c.asset == "SOL"
    assert c.direction == "above"
    assert c.target_price == 200.0


def test_parse_solana_alias():
    c = parse_contract("tok2", "Will Solana exceed $180 by March?")
    assert c.parseable
    assert c.asset == "SOL"


def test_parse_xrp():
    c = parse_contract("tok3", "Will XRP be above $1.50 by June?")
    assert c.parseable
    assert c.asset == "XRP"
    assert c.target_price == 1.50


def test_parse_ripple_alias():
    c = parse_contract("tok4", "Will Ripple reach $2 by May?")
    assert c.parseable
    assert c.asset == "XRP"


def test_parse_bnb():
    c = parse_contract("tok5", "Will BNB be above $700 by April?")
    assert c.parseable
    assert c.asset == "BNB"


def test_parse_doge():
    c = parse_contract("tok6", "Will DOGE hit $0.50 by end of March?")
    assert c.parseable
    assert c.asset == "DOGE"
    assert c.target_price == 0.50


def test_parse_dogecoin_alias():
    c = parse_contract("tok7", "Will Dogecoin be above $0.40 by April?")
    assert c.parseable
    assert c.asset == "DOGE"


def test_parse_ada():
    c = parse_contract("tok8", "Will ADA be above $1 by May?")
    assert c.parseable
    assert c.asset == "ADA"


def test_parse_avax():
    c = parse_contract("tok9", "Will AVAX exceed $50 by June?")
    assert c.parseable
    assert c.asset == "AVAX"


def test_btc_still_works():
    c = parse_contract("tok10", "Will BTC be above $90,000 by end of March?")
    assert c.parseable
    assert c.asset == "BTC"
    assert c.target_price == 90000.0


def test_eth_still_works():
    c = parse_contract("tok11", "Will ETH reach $4,000 by April?")
    assert c.parseable
    assert c.asset == "ETH"
    assert c.target_price == 4000.0


def test_unknown_asset_not_parseable():
    c = parse_contract("tok12", "Will LINK be above $20 by March?")
    assert not c.parseable
