import time
from market.state import FeedState


def test_feed_fresh_when_both_recent():
    fs = FeedState()
    now = time.time()
    fs.last_feed_update["clob"] = now
    fs.last_feed_update["microstructure"] = now
    assert fs.is_fresh(max_age_seconds=5)


def test_feed_stale_when_clob_old():
    fs = FeedState()
    fs.last_feed_update["clob"] = time.time() - 10
    fs.last_feed_update["microstructure"] = time.time()
    assert not fs.is_fresh(max_age_seconds=5)


def test_feed_stale_when_microstructure_missing():
    fs = FeedState()
    fs.last_feed_update["clob"] = time.time()
    # microstructure key absent
    assert not fs.is_fresh(max_age_seconds=5)


def test_feed_stale_when_both_missing():
    fs = FeedState()
    assert not fs.is_fresh(max_age_seconds=5)


def test_feed_stale_at_exact_boundary():
    fs = FeedState()
    now = time.time()
    fs.last_feed_update["clob"] = now - 5.0
    fs.last_feed_update["microstructure"] = now
    # exactly at boundary — should be stale (strict less-than)
    assert not fs.is_fresh(max_age_seconds=5.0)
