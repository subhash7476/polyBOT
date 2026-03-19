from engine.probability import build_model_probability
from engine.contract_parser import ParsedContract, parse_contract
from market.state import FeedState
from datetime import datetime, timezone, timedelta


def test_rates_contract_uses_fed_signal():
    fs = FeedState()
    fs.fed_may_cut_prob = 0.75
    fs.fed_confidence = 0.9

    contract = ParsedContract(
        token_id="fed_test",
        question="Will the Fed cut rates in May?",
        asset=None,
        direction="below",
        target_price=25.0,
        expiry=datetime.now(timezone.utc) + timedelta(days=30),
        category="rates",
        parseable=True,
    )

    prob, count, engine = build_model_probability(
        contract, fs, {"fed_cut_prob": 0.10}
    )
    assert count >= 1  # fed_cut_prob signal should fire
    assert prob != 0.5


def test_rates_contract_parsed_and_probability():
    """End-to-end: parse a rate contract, get probability."""
    fs = FeedState()
    fs.fed_may_cut_prob = 0.80
    fs.fed_confidence = 1.0

    contract = parse_contract("tok1", "Will the Fed cut rates by 25 basis points in May?")
    assert contract.parseable
    assert contract.category == "rates"

    prob, count, engine = build_model_probability(
        contract, fs, {"fed_cut_prob": 0.10}
    )
    assert count >= 1
    assert 0.0 < prob < 1.0


def test_rates_no_fed_signal_returns_neutral():
    """Without fed_may_cut_prob in feeds, prior stays 0.5."""
    fs = FeedState()  # no fed_may_cut_prob

    contract = ParsedContract(
        token_id="fed_test2",
        question="Will the Fed cut rates in May?",
        asset=None,
        direction="below",
        target_price=25.0,
        expiry=datetime.now(timezone.utc) + timedelta(days=30),
        category="rates",
        parseable=True,
    )

    prob, count, engine = build_model_probability(contract, fs, {"fed_cut_prob": 0.10})
    assert count == 0
    assert abs(prob - 0.5) < 0.01
