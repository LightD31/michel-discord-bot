"""Birthday dates: today's celebrations across timezones, 29 February, ages and order."""

from datetime import date, datetime

import pytest

from features.birthday import (
    BirthdayEntry,
    age_on,
    birthday_in_year,
    calendar_order,
    is_birthday,
    parse_birth_date,
    strip_year_from_format,
)


def entry(day: str, tz: str = "Europe/Paris") -> BirthdayEntry:
    return BirthdayEntry(user=1, date=day, timezone=tz)


def test_birthday_is_detected_on_the_members_own_date():
    alice = entry("14/07/1990")
    assert is_birthday(alice, date(2026, 7, 14))
    assert not is_birthday(alice, date(2026, 7, 13))
    assert not is_birthday(alice, date(2026, 7, 15))


def test_a_leap_day_birthday_is_celebrated_on_1_march_in_common_years():
    leap = entry("29/02/2000")
    assert birthday_in_year(leap.date, 2026) == date(2026, 3, 1)
    assert is_birthday(leap, date(2026, 3, 1))
    assert not is_birthday(leap, date(2026, 2, 28))
    assert is_birthday(leap, date(2028, 2, 29))
    assert not is_birthday(leap, date(2028, 3, 1))


def test_ages_count_full_years():
    born = datetime(2000, 7, 14)
    assert age_on(born, date(2026, 7, 13)) == 25
    assert age_on(born, date(2026, 7, 14)) == 26
    assert age_on(datetime(2000, 2, 29), date(2026, 3, 1)) == 26


def test_the_list_is_in_calendar_order_whatever_the_year():
    entries = [entry("01/03/1980"), entry("29/02/2004"), entry("31/12/1999"), entry("01/01/2010")]
    ordered = [e.date.strftime("%d/%m") for e in sorted(entries, key=calendar_order)]
    assert ordered == ["01/01", "29/02", "01/03", "31/12"]


def test_birth_dates_must_be_valid_and_not_in_the_future():
    today = date(2026, 3, 10)
    assert parse_birth_date("29/02/2000", today=today) == datetime(2000, 2, 29)
    with pytest.raises(ValueError, match="Format attendu"):
        parse_birth_date("2000-02-29", today=today)
    with pytest.raises(ValueError, match="Format attendu"):
        parse_birth_date("29/02/2001", today=today)
    with pytest.raises(ValueError, match="futur"):
        parse_birth_date("11/03/2026", today=today)


def test_entries_reject_an_unknown_timezone():
    with pytest.raises(ValueError):
        entry("01/01/2000", tz="Mars/Olympus_Mons")


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        ("d MMMM y", "d MMMM"),
        ("MMMM d, y", "MMMM d"),
        ("dd/MM/yyyy", "dd/MM"),
        ("y MMMM d", "MMMM d"),
    ],
)
def test_the_year_is_stripped_from_locale_formats(pattern, expected):
    assert strip_year_from_format(pattern) == expected
