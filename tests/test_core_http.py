"""Tests for the HTTP retry backoff schedule."""

from src.core.http import _MAX_BACKOFF_SECONDS, _backoff_delay


def test_backoff_grows_exponentially():
    # Jitter adds at most `pause` on top of the exponential base.
    for attempt, base in [(0, 1), (1, 2), (2, 4), (3, 8)]:
        delay = _backoff_delay(1, attempt)
        assert base <= delay <= base + 1


def test_backoff_is_capped():
    delay = _backoff_delay(10, 10)  # 10 * 2**10 ≫ cap
    assert delay <= _MAX_BACKOFF_SECONDS + 10
