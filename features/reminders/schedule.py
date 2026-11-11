"""When a reminder fires, and how its schedule reads.

Pure functions used by ``extensions/reminders``: the first occurrence of a new
reminder, the next one after it fires, and the label shown in
``/reminder remove``.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from dateutil.relativedelta import relativedelta

from features.reminders.models import Frequency, Reminder

DEFAULT_SNOOZE_MINUTES = 10
MAX_SNOOZE_MINUTES = 1440

_STEPS: dict[str, timedelta | relativedelta] = {
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
    "monthly": relativedelta(months=1),
    "yearly": relativedelta(years=1),
}
_WEEKDAYS = ("lundis", "mardis", "mercredis", "jeudis", "vendredis", "samedis", "dimanches")


class PastReminderError(ValueError):
    """A one-shot reminder was asked for a moment that has already passed."""


def next_occurrence(when: datetime, frequency: Frequency | None) -> datetime | None:
    """The occurrence one period after *when*; ``None`` for a one-shot reminder."""
    if frequency is None:
        return None
    return when + _STEPS[frequency]


def next_after(when: datetime, frequency: Frequency | None, now: datetime) -> datetime | None:
    """The first occurrence strictly after *now*, skipping any missed while the bot was down."""
    upcoming = next_occurrence(when, frequency)
    while upcoming is not None and upcoming <= now:
        upcoming = next_occurrence(upcoming, frequency)
    return upcoming


def first_occurrence(
    now: datetime,
    *,
    hour: int,
    minute: int,
    frequency: Frequency | None,
    start: date | None = None,
) -> datetime:
    """When a reminder created at *now* first fires.

    Without *start*, a time already past today means tomorrow. A recurring
    reminder whose start is in the past fires at its next occurrence. A
    one-shot reminder on a past date raises :class:`PastReminderError`.
    """
    day = start or now.date()
    when = datetime(day.year, day.month, day.day, hour, minute)
    if when > now:
        return when
    if frequency is not None:
        while when <= now:
            when += _STEPS[frequency]
        return when
    if start is None:
        return when + timedelta(days=1)
    raise PastReminderError("Le rappel ne peut pas être dans le passé.")


def snooze_minutes(guild_config: dict[str, Any]) -> int:
    """The guild's snooze delay: ``reminderSnoozeMinutes`` clamped to 1 min – 24 h."""
    raw = guild_config.get("reminderSnoozeMinutes")
    try:
        value = int(raw) if raw is not None else DEFAULT_SNOOZE_MINUTES
    except (TypeError, ValueError):
        value = DEFAULT_SNOOZE_MINUTES
    return max(1, min(value, MAX_SNOOZE_MINUTES))


def schedule_label(reminder: Reminder) -> str:
    """How the reminder's schedule reads in French, e.g. ``Tous les lundis à 08:30``."""
    when = reminder.remind_time
    at = when.strftime("%H:%M")
    if reminder.frequency == "daily":
        return f"Tous les jours à {at}"
    if reminder.frequency == "weekly":
        return f"Tous les {_WEEKDAYS[when.weekday()]} à {at}"
    if reminder.frequency == "monthly":
        return f"Tous les {when.day} du mois à {at}"
    if reminder.frequency == "yearly":
        return f"Tous les {when.strftime('%d/%m')} à {at}"
    return f"Le {when.strftime('%d/%m/%Y')} à {at}"


__all__ = [
    "DEFAULT_SNOOZE_MINUTES",
    "PastReminderError",
    "first_occurrence",
    "next_after",
    "next_occurrence",
    "schedule_label",
    "snooze_minutes",
]
