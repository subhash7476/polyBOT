import pytest
from trading.slippage import SlippageEstimate
from trading.ev_gate import calculate_ev, should_enter, get_trade_direction


def make_slip(adjusted_price: float, slippage_pct: float = 0.005,
              tradeable: bool = True) -> SlippageEstimate:
    return SlippageEstimate(adjusted_price=adjusted_price,
                            slippage_pct=slippage_pct,
                            tradeable=tradeable)


# --- get_trade_direction (v2.1 Fix 1) ---

def test_buys_yes_when_model_above_market():
    side, _ = get_trade_direction(model_prob=0.65, market_price=0.50)
    assert side == "BUY_YES"


def test_buys_no_when_model_below_market():
    side, _ = get_trade_direction(model_prob=0.35, market_price=0.50)
    assert side == "BUY_NO"


def test_buy_yes_relevant_price_is_yes_ask():
    _, price = get_trade_direction(model_prob=0.65, market_price=0.50)
    assert price == 0.50


def test_buy_no_relevant_price_is_no_ask():
    _, price = get_trade_direction(model_prob=0.35, market_price=0.50)
    assert abs(price - 0.50) < 1e-9  # NO ask = 1 - YES bid = 1 - 0.50 = 0.50


# --- calculate_ev (v2.1 Fix 4: spread_penalty + adverse_selection) ---

def test_positive_ev_when_model_well_above_market():
    slip = make_slip(adjusted_price=0.52)
    ev, side = calculate_ev(model_prob=0.70, market_price=0.50, slippage=slip)
    assert ev > 0
    assert side == "BUY_YES"


def test_negative_ev_when_model_close_to_market():
    # model=0.51 market=0.50: marginal edge but fees+penalties kill it
    slip = make_slip(adjusted_price=0.51)
    ev, side = calculate_ev(model_prob=0.51, market_price=0.50, slippage=slip)
    assert ev < 0


def test_ev_worse_with_spread_penalty():
    # Compare EV with and without spread (different adjusted prices)
    slip_tight = make_slip(adjusted_price=0.50)
    slip_wide  = make_slip(adjusted_price=0.55)
    ev_tight, _ = calculate_ev(0.65, 0.50, slip_tight)
    ev_wide,  _ = calculate_ev(0.65, 0.50, slip_wide)
    assert ev_tight > ev_wide


def test_buy_no_ev_calculated_correctly():
    # model=0.30 market=0.60 → BUY_NO: effective_prob = 1-0.30 = 0.70
    # NO ask = 1 - 0.60 = 0.40
    slip = make_slip(adjusted_price=0.41)  # slightly above NO ask due to slippage
    ev, side = calculate_ev(model_prob=0.30, market_price=0.60, slippage=slip)
    assert side == "BUY_NO"
    assert ev > 0  # 0.70 payout - (0.41 + fees + penalties) > 0


def test_ev_returns_tuple_of_float_and_str():
    slip = make_slip(adjusted_price=0.52)
    result = calculate_ev(0.65, 0.50, slip)
    assert isinstance(result, tuple)
    assert isinstance(result[0], float)
    assert isinstance(result[1], str)


# --- should_enter ---

def test_enters_above_threshold():
    slip = make_slip(adjusted_price=0.52)
    ok, reason = should_enter(ev=0.05, slippage=slip, ev_multiplier=1.0)
    assert ok is True


def test_rejects_below_threshold():
    slip = make_slip(adjusted_price=0.52)
    ok, reason = should_enter(ev=0.01, slippage=slip, ev_multiplier=1.0)
    assert ok is False


def test_rejects_untradeable_market():
    slip = make_slip(adjusted_price=0.52, tradeable=False)
    ok, reason = should_enter(ev=0.10, slippage=slip, ev_multiplier=1.0)
    assert ok is False
    assert "thin" in reason


def test_ev_multiplier_raises_threshold():
    slip = make_slip(adjusted_price=0.52)
    # ev=0.05 passes at multiplier=1 but fails at multiplier=2 (threshold=0.06)
    ok_1x, _ = should_enter(ev=0.05, slippage=slip, ev_multiplier=1.0)
    ok_2x, _ = should_enter(ev=0.05, slippage=slip, ev_multiplier=2.0)
    assert ok_1x is True
    assert ok_2x is False
