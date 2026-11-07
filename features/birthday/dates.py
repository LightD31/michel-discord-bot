"""Birthday dates: today's celebrations, ages, and the order of the list.

Born on 29 February? The birthday falls on 1 March in common years.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from dateutil.relativedelta import relativedelta

from features.birthday.models import BirthdayEntry

DATE_FORMAT = "%d/%m/%Y"


def parse_birth_date(text: str, *, today: date) -> datetime:
    """A ``JJ/MM/AAAA`` date that isn't in the future; ``ValueError`` (in French) otherwise."""
    try:
        parsed = datetime.strptime(text, DATE_FORMAT)
    except ValueError as e:
        raise ValueError("Date invalide. Format attendu : JJ/MM/AAAA") from e
    if parsed.date() > today:
        raise ValueError("Cette date est dans le futur.")
    return parsed


def birthday_in_year(birth: date | datetime, year: int) -> date:
    """The day the birthday falls on in *year* (29 February → 1 March in a common year)."""
    try:
        return date(year, birth.month, birth.day)
    except ValueError:
        return date(year, 3, 1)


def age_on(birth: date | datetime, day: date) -> int:
    """Age in full years on *day*."""
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
