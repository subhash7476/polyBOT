from datetime import datetime, timezone, timedelta

from engine.category_probability import build_category_probability
from engine.contract_parser import ParsedContract
from market.state import ContractState, FeedState


def test_category_probability_adds_signals():
    contract = ParsedContract(
        token_id="tok1",
        question="Will the Lakers win tonight?",
        category="sports",
        parseable=True,
        expiry=datetime.now(timezone.utc) + timedelta(hours=12),
    )
    cs = ContractState(
        yes_token_id="tok1",
        no_token_id="tok2",
        question=contract.question,
        category="sports",
        best_bid=0.42,
        best_ask=0.58,
        end_date_iso=(datetime.now(timezone.utc) + timedelta(hours=12)).isoformat(),
    )
    prob, count, engine = build_category_probability(contract, cs, FeedState(), {})
    assert count >= 2
    assert 0.0 < prob < 1.0
    assert engine.signal_count == count
