"""What the agenda lists, and how it reads day by day (P19, A5).

An :class:`Entry` is one thing coming up on the server: a Discord scheduled
event, a birthday, a giveaway or a poll closing, the Secret Santa exchange.
Personal reminders are never listed: they are one member's business.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

from features.secretsanta.dates import MONTH_NAMES
from src.core import clock

Kind = Literal["event", "birthday", "giveaway", "poll", "santa"]
ICONS: dict[str, str] = {
    "event": "📅",
    "birthday": "🎂",
    "giveaway": "🎁",
    "poll": "📊",
    "santa": "🎅",
}
WEEKDAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
MIN_DAYS, MAX_DAYS, DEFAULT_DAYS = 3, 31, 14
LINES_PER_DAY = 10  # a day with more says how many it left out


@dataclass(frozen=True)
class Entry:
    day: date  # the local (Paris) day it falls on
    kind: Kind
    title: str
    at: datetime | None = None  # its time, aware; None for a whole day (a birthday)
    url: str | None = None

    @property
    def icon(self) -> str:
        return ICONS[self.kind]


def bounded_days(value: object) -> int:
    try:
        days = int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return DEFAULT_DAYS
    return max(MIN_DAYS, min(days, MAX_DAYS))


def window(now: datetime, days: int) -> tuple[datetime, datetime]:
    """From *now* to the end of the *days*-th local day (today counts as the first)."""
    last = clock.to_local(now).date() + timedelta(days=days)
    return now, clock.at_local(last)


def timed(kind: Kind, title: str, at: datetime, url: str | None = None) -> Entry:
    return Entry(clock.to_local(at).date(), kind, title, at, url)


def day_label(day: date, today: date) -> str:
    if day == today:
        return "Aujourd'hui"
    if day == today + timedelta(days=1):
        return "Demain"
    first = "1er" if day.day == 1 else str(day.day)
    return f"{WEEKDAYS[day.weekday()].capitalize()} {first} {MONTH_NAMES[day.month - 1]}"


def sort_entries(entries: list[Entry]) -> list[Entry]:
    """By day, whole days first, then by time and title."""

    def key(entry: Entry) -> tuple:
        moment = clock.to_local(entry.at).time() if entry.at else None
        return (entry.day, moment is not None, moment or datetime.min.time(), entry.title)

    return sorted(entries, key=key)


def entry_line(entry: Entry) -> str:
    when = f"{clock.to_local(entry.at):%H:%M} · " if entry.at else ""
    title = f"[{entry.title}]({entry.url})" if entry.url else entry.title
    return f"{entry.icon} {when}{title}"


def by_day(entries: list[Entry], today: date) -> list[tuple[str, list[str]]]:
    """``(day label, lines)`` for each day that has something, soonest first."""
    days: dict[date, list[Entry]] = {}
    for entry in sort_entries(entries):
        days.setdefault(entry.day, []).append(entry)
    out = []
    for day, found in days.items():
        lines = [entry_line(e) for e in found[:LINES_PER_DAY]]
        if len(found) > LINES_PER_DAY:
            lines.append(f"… et {len(found) - LINES_PER_DAY} de plus")
        out.append((day_label(day, today), lines))
    return out


__all__ = [
    "DEFAULT_DAYS",
    "ICONS",
    "MAX_DAYS",
    "MIN_DAYS",
    "Entry",
    "Kind",
    "bounded_days",
    "by_day",
    "day_label",
    "entry_line",
    "sort_entries",
    "timed",
    "window",
]
