from feeds.onchain import normalise, parse_timeseries


def test_normalise_midrange():
    result = normalise(0.0, -10000, 10000)
    assert abs(result - 0.5) < 0.001


def test_normalise_at_max_clamps_to_one():
    result = normalise(99999, 0, 100)
    assert result == 1.0


def test_normalise_at_min_clamps_to_zero():
    result = normalise(-99999, 0, 100)
    assert result == 0.0


def test_normalise_invert():
    # High inflow (positive netflow) → bearish → low bullish signal
    result = normalise(5000, -10000, 10000, invert=True)
    assert result < 0.5


def test_normalise_invert_negative_is_bullish():
    result = normalise(-5000, -10000, 10000, invert=True)
    assert result > 0.5


def test_parse_timeseries_returns_last():
    ts = [{"t": 1000, "v": 0.3}, {"t": 2000, "v": 0.7}]
    assert parse_timeseries(ts) == 0.7


def test_parse_timeseries_empty_returns_none():
    assert parse_timeseries([]) is None
