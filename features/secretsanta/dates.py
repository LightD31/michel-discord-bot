"""The exchange date of a Secret Santa session, and what hangs off it.

The organiser types it ("24/12", "24 décembre", "24/12/2026"); it is kept as a
calendar day (``YYYY-MM-DD``). It is shown in the draw DM, sets when the
"gift not sent yet" reminder goes out (sessions with parcels only), and when
the wish lists and gift marks are deleted.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from src.core import clock

MONTHS = {
    "janvier": 1,
    "janv": 1,
    "février": 2,
    "fevrier": 2,
    "févr": 2,
    "fevr": 2,
    "mars": 3,
    "avril": 4,
    "avr": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "juil": 7,
    "août": 8,
    "aout": 8,
    "septembre": 9,
    "sept": 9,
    "octobre": 10,
    "oct": 10,
    "novembre": 11,
    "nov": 11,
    "décembre": 12,
    "decembre": 12,
    "déc": 12,
    "dec": 12,
}
MONTH_NAMES = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)
WEEKDAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")

# A date further than this is a typo (a year too many), not a plan.
MAX_DAYS_AHEAD = 400
# The 10:00 Paris reminder, so nobody is woken by it.
REMINDER_HOUR = 10
# Wish lists and gift marks outlive the exchange by this much (Privacy.md §7),
# or, for a session without a date, its creation by NO_DATE_KEEP.
KEEP_AFTER_EXCHANGE = timedelta(days=30)
NO_DATE_KEEP = timedelta(days=90)

UNREADABLE = "Date non reconnue : écris-la JJ/MM, JJ/MM/AAAA ou « 24 décembre »."

_NUMERIC = re.compile(r"(\d{1,2})[/.\-](\d{1,2})(?:[/.\-](\d{2}|\d{4}))?")
_WORDS = re.compile(r"(\d{1,2})(?:er)?\s+([a-zéû]+)\.?(?:\s+(\d{4}))?")
_FILLER = re.compile(r"^(?:le\s+)?(?:(?:" + "|".join(WEEKDAYS) + r")\s+)?")


def parse_exchange_date(text: str, today: date) -> date:
    """The day *text* names, from *today*'s point of view; ``ValueError`` (French) otherwise.

    Without a year it is the next such day (today included): "24/12" typed
    on 30 December is next year's.
    """
    cleaned = _FILLER.sub("", " ".join(text.lower().split()))
    numeric, words = _NUMERIC.fullmatch(cleaned), _WORDS.fullmatch(cleaned)
    if numeric:
        day, month, year_text = int(numeric[1]), int(numeric[2]), numeric[3]
    elif words and words[2] in MONTHS:
        day, month, year_text = int(words[1]), MONTHS[words[2]], words[3]
    else:
        raise ValueError(UNREADABLE)
    year = int(year_text) if year_text else today.year
    if year_text and len(year_text) == 2:
        year += 2000
    try:
        result = date(year, month, day)
        if not year_text and result < today:
            result = date(year + 1, month, day)
    except ValueError:
        raise ValueError(UNREADABLE) from None
    if result < today:
        raise ValueError("Cette date est déjà passée.")
    if (result - today).days > MAX_DAYS_AHEAD:
        raise ValueError("Cette date est trop lointaine : vérifie l'année.")
    return result


def format_exchange_date(day: date) -> str:
    """``jeudi 24 décembre 2026``."""
    return f"{WEEKDAYS[day.weekday()]} {day.day} {MONTH_NAMES[day.month - 1]} {day.year}"


def reminder_due(exchange: date, days_before: int, now: datetime) -> bool:
    """Whether the parcel reminder should go out at *now*.

    From 10:00 Paris *days_before* days ahead of the exchange until the day
    itself is over: a bot that was down that morning still sends it later,
    never after the exchange.
    """
    start = clock.at_local(exchange - timedelta(days=days_before), REMINDER_HOUR)
    end = clock.at_local(exchange + timedelta(days=1))
    return start <= now < end


def participant_expiry(exchange: date | None, created_at: datetime) -> datetime:
    """When a participant's wish list and gift marks are deleted (a TTL index)."""
    if exchange is not None:
        return clock.at_local(exchange) + KEEP_AFTER_EXCHANGE
    return created_at + NO_DATE_KEEP


__all__ = [
    "MAX_DAYS_AHEAD",
    "format_exchange_date",
    "parse_exchange_date",
    "participant_expiry",
    "reminder_due",
]
