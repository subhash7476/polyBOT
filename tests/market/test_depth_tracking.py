from market.state import ContractState


def test_contract_state_has_depth_fields():
    cs = ContractState(
        yes_token_id="abc", no_token_id="def",
        question="Will BTC > $100k?", category="crypto"
    )
    assert hasattr(cs, "bid_depth")
    assert hasattr(cs, "ask_depth")
    assert cs.bid_depth == 0.0
    assert cs.ask_depth == 0.0


def test_bid_depth_defaults_zero():
    cs = ContractState(
        yes_token_id="abc", no_token_id="def",
        question="test", category="crypto",
        best_bid=0.60, best_ask=0.65
    )
    assert cs.bid_depth == 0.0
    assert cs.ask_depth == 0.0
