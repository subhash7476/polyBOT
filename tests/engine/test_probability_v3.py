from datetime import datetime, timezone, timedelta
from engine.probability import build_model_probability
from engine.contract_parser import ParsedContract
from market.state import FeedState


def _make_feeds(**kwargs) -> FeedState:
    fs = FeedState()
    for k, v in kwargs.items():
        setattr(fs, k, v)
    return fs


def _make_contract(asset="BTC", target=90000, direction="above") -> ParsedContract:
    return ParsedContract(
        token_id="test",
        question=f"Will {asset} be {direction} ${target}?",
        asset=asset,
        direction=direction,
        target_price=float(target),
        expiry=datetime.now(timezone.utc) + timedelta(days=7),
        category="crypto",
        parseable=True,
    )


def test_sol_uses_sol_spot_and_dvol():
    fs = FeedState()
    fs.spot_prices["SOL"] = 140.0
    fs.dvol["SOL"] = 80.0
    contract = _make_contract(asset="SOL", target=200)
    prob, count, engine = build_model_probability(contract, fs, {})
    assert 0.0 < prob < 1.0
    assert prob < 0.5  # spot 140, target 200 → unlikely in 7 days


def test_btc_still_works_after_refactor():
    fs = FeedState()
    fs.spot_prices["BTC"] = 85000.0
    fs.dvol["BTC"] = 72.0
    contract = _make_contract(asset="BTC", target=90000)
    prob, count, engine = build_model_probability(contract, fs, {})
    assert 0.0 < prob < 1.0


def test_missing_spot_returns_neutral():
    fs = FeedState()
    # No spot price for DOGE
    contract = _make_contract(asset="DOGE", target=0.5)
    prob, count, engine = build_model_probability(contract, fs, {})
    assert prob == 0.5


def test_funding_rate_signal_for_sol():
    fs = FeedState()
    fs.spot_prices["SOL"] = 140.0
    fs.dvol["SOL"] = 80.0
    fs.funding_rates["SOL"] = 0.001  # positive = crowded longs
    contract = _make_contract(asset="SOL", target=200)
    prob, count, engine = build_model_probability(
        contract, fs, {"funding_rate": 0.15}
    )
    assert count >= 1  # funding_rate signal active


def test_xrp_with_spot_and_dvol():
    fs = FeedState()
    fs.spot_prices["XRP"] = 2.0
    fs.dvol["XRP"] = 90.0
    contract = _make_contract(asset="XRP", target=5.0)
    prob, _, _ = build_model_probability(contract, fs, {})
    # XRP at $2, target $5 in 7 days — prior should be well below 0.5
    assert prob < 0.3


def test_vol_skew_for_btc():
    fs = FeedState()
    fs.spot_prices["BTC"] = 85000.0
    fs.dvol["BTC"] = 72.0
    fs.vol_skew["BTC"] = 5.0  # puts bid up → bearish signal
    contract = _make_contract(asset="BTC", target=90000, direction="above")
    prob, count, _ = build_model_probability(contract, fs, {"vol_skew": 0.15})
    assert count >= 1
