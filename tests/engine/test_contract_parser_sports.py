from engine.contract_parser import parse_contract


def test_nba_game():
    p = parse_contract("tok1", "Will the Los Angeles Lakers beat the Boston Celtics on April 5?")
    assert p.category == "sports"
    assert p.parseable is True


def test_nfl_game():
    p = parse_contract("tok2", "Will the Kansas City Chiefs win Super Bowl LX?")
    assert p.category == "sports"
    assert p.parseable is True


def test_mlb_game():
    p = parse_contract("tok3", "Will the New York Yankees win the World Series?")
    assert p.category == "sports"
    assert p.parseable is True


def test_soccer_match():
    p = parse_contract("tok4", "Will Real Madrid win the Champions League 2026?")
    assert p.category == "sports"
    assert p.parseable is True


def test_generic_team_win():
    p = parse_contract("tok5", "Will the Packers defeat the Bears on Sunday?")
    assert p.category == "sports"
    assert p.parseable is True


def test_non_sports_not_matched():
    p = parse_contract("tok6", "Will BTC be above $100k on April 30?")
    assert p.category != "sports"


def test_election_not_matched_as_sports():
    p = parse_contract("tok7", "Will the Democrats win the Senate in 2026?")
    assert p.category != "sports"
