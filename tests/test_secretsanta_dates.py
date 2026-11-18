"""The exchange date as organisers type it, the reminder window, the expiry, the relay limits."""

from datetime import UTC, date, datetime, timedelta

import pytest

from features.secretsanta.dates import (
    format_exchange_date,
    parse_exchange_date,
    participant_expiry,
    reminder_due,
)
from features.secretsanta.relay import BURST, PER_DAY, RelayLimiter
from src.core import clock

TODAY = date(2026, 11, 17)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("24/12", date(2026, 12, 24)),
        ("24/12/2026", date(2026, 12, 24)),
        ("24-12-26", date(2026, 12, 24)),
        ("24.12", date(2026, 12, 24)),
        ("24 décembre", date(2026, 12, 24)),
        ("24 Décembre 2026", date(2026, 12, 24)),
        ("le 24 decembre", date(2026, 12, 24)),
        ("jeudi 24 déc.", date(2026, 12, 24)),
        ("1er janvier", date(2027, 1, 1)),
        ("17/11", TODAY),  # today is still ahead
        ("16/11", date(2027, 11, 16)),  # without a year: the next one
        ("5/1", date(2027, 1, 5)),
    ],
)
def test_dates_as_organisers_type_them(text, expected):
    assert parse_exchange_date(text, TODAY) == expected


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("Noël", "Date non reconnue"),
        ("31/02", "Date non reconnue"),
        ("24 brumaire", "Date non reconnue"),
        ("24/13/2026", "Date non reconnue"),
        ("1/1/2026", "déjà passée"),
        ("24/12/2028", "trop lointaine"),
    ],
)
def test_what_is_refused_says_why(text, message):
    with pytest.raises(ValueError, match=message):
        parse_exchange_date(text, TODAY)


def test_a_date_reads_in_french():
    assert format_exchange_date(date(2026, 12, 24)) == "jeudi 24 décembre 2026"
    assert format_exchange_date(date(2027, 1, 1)) == "vendredi 1 janvier 2027"


def paris(day, hour=0, minute=0):
    return clock.at_local(day, hour, minute)


@pytest.mark.parametrize(
    ("now", "due"),
    [
        (paris(date(2026, 12, 21), 9, 59), False),
        (paris(date(2026, 12, 21), 10), True),  # 3 days before, 10:00 Paris
        (paris(date(2026, 12, 23), 18), True),  # a bot down that morning still sends it
        (paris(date(2026, 12, 24), 23, 59), True),
        (paris(date(2026, 12, 25)), False),  # never after the exchange
    ],
)
def test_the_reminder_window(now, due):
    assert reminder_due(date(2026, 12, 24), 3, now) is due


def test_entries_expire_30_days_after_the_exchange_or_90_after_creation():
    expiry = participant_expiry(date(2026, 12, 24), datetime(2026, 11, 1, tzinfo=UTC))
    assert expiry == paris(date(2027, 1, 23))
    created = datetime(2026, 11, 1, tzinfo=UTC)
    assert participant_expiry(None, created) == created + timedelta(days=90)


def test_the_relay_allows_a_burst_then_waits():
    limiter, key = RelayLimiter(), ("guild_1", 3, "write")
    start = datetime(2026, 12, 1, 12, tzinfo=UTC)
    for i in range(BURST):
        assert limiter.retry_at(key, start + timedelta(seconds=i)) is None
        limiter.record(key, start + timedelta(seconds=i))
    later = start + timedelta(minutes=1)
    assert limiter.retry_at(key, later) == start + timedelta(minutes=10)
    assert limiter.retry_at(("guild_1", 3, "reply"), later) is None  # each direction its own
    assert limiter.retry_at(key, start + timedelta(minutes=10)) is None


def test_the_relay_caps_a_day():
    limiter, key = RelayLimiter(), ("guild_1", 3, "write")
    start = datetime(2026, 12, 1, tzinfo=UTC)
    for i in range(PER_DAY):
        limiter.record(key, start + timedelta(minutes=15 * i))
    after = start + timedelta(minutes=15 * PER_DAY)
    assert limiter.retry_at(key, after) == start + timedelta(days=1)
    assert limiter.retry_at(key, start + timedelta(days=1)) is None


def test_a_cancelled_session_forgets_its_counters():
    limiter = RelayLimiter()
    now = datetime(2026, 12, 1, tzinfo=UTC)
    for _ in range(BURST):
        limiter.record(("guild_1", 3, "write"), now)
        limiter.record(("guild_2", 3, "write"), now)
    limiter.forget("guild_1")
    assert limiter.retry_at(("guild_1", 3, "write"), now) is None
    assert limiter.retry_at(("guild_2", 3, "write"), now) is not None
