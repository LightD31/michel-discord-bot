"""Birthday dates: today's celebrations, ages, and the order of the list.

Born on 29 February? The birthday falls on 1 March in common years.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from dateutil.relativedelta import relativedelta

from features.birthday.models import BirthdayEntry
from features.secretsanta.dates import MONTHS

# The year a birthday typed without one is kept in: a leap year, so 29
# February stays a day. Nobody on Discord was born in it.
NO_YEAR = 1904
MIN_YEAR = 1905
UNREADABLE = "Je n'ai pas compris « {text} ». Exemples : 12/03, 12/03/1990, 12 mars."
_NUMERIC = re.compile(r"(\d{1,2})[/.\-](\d{1,2})(?:[/.\-](\d{4}))?")
_WORDS = re.compile(r"(\d{1,2})(?:er)?\s+([a-zéû]+)\.?(?:\s+(\d{4}))?")


def parse_birth_date(text: str, *, today: date) -> datetime:
    """A birth date as a member types it: "12/03/1990", "12/03", "12 mars 1990", "1er mai".

    Without a year, the day is kept in :data:`NO_YEAR` (:func:`has_year`):
    the birthday is wished, no age is said. A date with a year may not be in
    the future. ``ValueError`` (in French, with examples) otherwise.
    """
    cleaned = " ".join(text.lower().split())
    numeric = _NUMERIC.fullmatch(cleaned)
    words = _WORDS.fullmatch(cleaned)
    if numeric:
        day, month, year = int(numeric[1]), int(numeric[2]), numeric[3]
    elif words and words[2] in MONTHS:
        day, month, year = int(words[1]), MONTHS[words[2]], words[3]
    else:
        raise ValueError(UNREADABLE.format(text=text.strip()))
    try:
        parsed = datetime(int(year) if year else NO_YEAR, month, day)
    except ValueError as e:
        raise ValueError(UNREADABLE.format(text=text.strip())) from e
    if year is None:
        return parsed
    if parsed.year < MIN_YEAR:
        raise ValueError("Vérifie l'année : elle a l'air trop ancienne.")
    if parsed.date() > today:
        raise ValueError("Cette date est dans le futur.")
    return parsed


def has_year(birth: date | datetime) -> bool:
    """Whether the member gave their year of birth (and so an age can be said)."""
    return birth.year != NO_YEAR


def birthday_in_year(birth: date | datetime, year: int) -> date:
    """The day the birthday falls on in *year* (29 February → 1 March in a common year)."""
    try:
        return date(year, birth.month, birth.day)
    except ValueError:
        return date(year, 3, 1)


def age_on(birth: date | datetime, day: date) -> int:
    """Age in full years on *day* (meaningless without :func:`has_year`)."""
    birth_day = birth.date() if isinstance(birth, datetime) else birth
    return relativedelta(day, birth_day).years


def is_birthday(entry: BirthdayEntry, local_today: date) -> bool:
    """Whether *local_today* (the date in the member's own timezone) is their birthday."""
    return birthday_in_year(entry.date, local_today.year) == local_today


def calendar_order(entry: BirthdayEntry) -> tuple[int, int]:
    """Sort key putting birthdays in calendar order, whatever the birth year."""
    return entry.date.month, entry.date.day


def strip_year_from_format(date_format: str) -> str:
    """Remove year tokens (y, yy, yyyy…) from a babel/ICU date format string."""
    cleaned = re.sub(r"[,/\-.\s]*y+[,/\-.\s]*", " ", date_format)
    return cleaned.strip(" ,.-/")


__all__ = [
    "age_on",
    "birthday_in_year",
    "calendar_order",
    "is_birthday",
    "parse_birth_date",
    "strip_year_from_format",
]
