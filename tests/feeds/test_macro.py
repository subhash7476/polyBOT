import pytest
from datetime import datetime, timezone, timedelta
from feeds.macro import CachedValue


def test_cached_value_fresh_is_not_stale():
    cv = CachedValue(value=104.0, fetched_at=datetime.now(timezone.utc), max_age_seconds=600)
    assert cv.is_stale is False


def test_cached_value_old_is_stale():
    old = datetime.now(timezone.utc) - timedelta(seconds=700)
    cv = CachedValue(value=104.0, fetched_at=old, max_age_seconds=600)
    assert cv.is_stale is True


def test_cached_value_confidence_fresh_is_one():
    cv = CachedValue(value=104.0, fetched_at=datetime.now(timezone.utc), max_age_seconds=600)
    assert cv.confidence > 0.99


def test_cached_value_confidence_old_is_zero():
    old = datetime.now(timezone.utc) - timedelta(seconds=700)
    cv = CachedValue(value=104.0, fetched_at=old, max_age_seconds=600)
    assert cv.confidence == 0.0


def test_cached_value_confidence_half_age():
    half_age = datetime.now(timezone.utc) - timedelta(seconds=300)
    cv = CachedValue(value=104.0, fetched_at=half_age, max_age_seconds=600)
    assert abs(cv.confidence - 0.5) < 0.05
