"""When a reminder fires, and how its schedule reads.

Pure functions used by ``extensions/reminders``: the next occurrence after a
reminder fires, and the label shown in ``/rappel liste``. Its first one is
what the member typed in « quand » (:mod:`features.reminders.when`).

Occurrences are UTC instants; the hour a member chose is a wall time in
:data:`src.core.clock.LOCAL_TZ`, so periods are counted on the wall clock: a
daily 20:00 reminder stays at 20:00 across a daylight saving change.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from dateutil.relativedelta import relativedelta

from features.reminders.models import Frequency, Reminder
from src.core.clock import LOCAL_TZ, to_local

DEFAULT_SNOOZE_MINUTES = 10
MAX_SNOOZE_MINUTES = 1440

_STEPS: dict[str, timedelta | relativedelta] = {
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
    "monthly": relativedelta(months=1),
    "yearly": relativedelta(years=1),
}
_WEEKDAYS = ("lundis", "mardis", "mercredis", "jeudis", "vendredis", "samedis", "dimanches")


def _step(when: datetime, frequency: Frequency) -> datetime:
    """One period after *when*, counted on the community's wall clock."""
    wall = to_local(when).replace(tzinfo=None) + _STEPS[frequency]
    return wall.replace(tzinfo=LOCAL_TZ).astimezone(UTC)


def next_occurrence(when: datetime, frequency: Frequency | None) -> datetime | None:
    """The occurrence one period after *when*; ``None`` for a one-shot reminder."""
    if frequency is None:
        return None
    return _step(when, frequency)


def next_after(when: datetime, frequency: Frequency | None, now: datetime) -> datetime | None:
    """The first occurrence strictly after *now*, skipping any missed while the bot was down."""
    upcoming = next_occurrence(when, frequency)
    while upcoming is not None and upcoming <= now:
        upcoming = next_occurrence(upcoming, frequency)
    return upcoming


def snooze_minutes(guild_config: dict[str, Any]) -> int:
    """The guild's snooze delay: ``reminderSnoozeMinutes`` clamped to 1 min – 24 h."""
    raw = guild_config.get("reminderSnoozeMinutes")
    try:
        value = int(raw) if raw is not None else DEFAULT_SNOOZE_MINUTES
    except (TypeError, ValueError):
        value = DEFAULT_SNOOZE_MINUTES
    return max(1, min(value, MAX_SNOOZE_MINUTES))


SNOOZE_MARK = "⏰ (snooze) "


def snoozed_text(text: str | None) -> str:
    """The text of a snoozed reminder: marked once, however many times it was snoozed."""
    text = text or "Rappel"
    while text.startswith(SNOOZE_MARK):
        text = text.removeprefix(SNOOZE_MARK)
    return SNOOZE_MARK + text


def schedule_label(reminder: Reminder) -> str:
    """How the reminder's schedule reads in French, e.g. ``Tous les lundis à 08:30``."""
    when = to_local(reminder.remind_time)
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
    "next_after",
    "next_occurrence",
    "schedule_label",
    "snooze_minutes",
    "snoozed_text",
]
