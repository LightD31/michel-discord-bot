"""When a reminder fires again: catch-up after downtime, daylight saving, labels.

Its first time is read from « quand » (``tests/test_reminders_when.py``)."""

from datetime import datetime

import pytest

from features.reminders import (
    Reminder,
    next_after,
    schedule_label,
    snooze_minutes,
    snoozed_text,
)
from src.core.clock import LOCAL_TZ


def paris(*args):
    """A wall time on the community's clock: what members type and see."""
    return datetime(*args, tzinfo=LOCAL_TZ)


NOW = paris(2026, 3, 10, 12, 0)  # a Tuesday


def test_next_after_skips_occurrences_missed_while_the_bot_was_down():
    fired = paris(2026, 3, 1, 9, 0)
    assert next_after(fired, "daily", NOW) == paris(2026, 3, 11, 9, 0)
    assert next_after(fired, "weekly", NOW) == paris(2026, 3, 15, 9, 0)
    assert next_after(fired, None, NOW) is None


def test_next_after_steps_one_period_when_on_time():
    fired = paris(2026, 3, 10, 11, 59)
    assert next_after(fired, "monthly", NOW) == paris(2026, 4, 10, 11, 59)
    assert next_after(paris(2024, 2, 29, 8, 0), "yearly", NOW) == paris(2027, 2, 28, 8, 0)


@pytest.mark.parametrize(
    ("configured", "expected"),
    [(None, 10), (5, 5), ("15", 15), (0, 1), (-3, 1), (10_000, 1440), ("abc", 10)],
)
def test_snooze_minutes_are_clamped(configured, expected):
    cfg = {} if configured is None else {"reminderSnoozeMinutes": configured}
    assert snooze_minutes(cfg) == expected


@pytest.mark.parametrize(
    ("frequency", "label"),
    [
        (None, "Le 10/03/2026 à 08:05"),
        ("daily", "Tous les jours à 08:05"),
        ("weekly", "Tous les mardis à 08:05"),
        ("monthly", "Tous les 10 du mois à 08:05"),
        ("yearly", "Tous les 10/03 à 08:05"),
    ],
)
def test_schedule_labels_read_in_french(frequency, label):
    reminder = Reminder(
        user_id="1", message="m", remind_time=paris(2026, 3, 10, 8, 5), frequency=frequency
    )
    assert schedule_label(reminder) == label


def test_a_stored_shared_reminder_still_loads():
    """Documents written before reminders went to their author only keep loading."""
    reminder = Reminder(user_id="1", message="m", remind_time=NOW, recipient_ids=["2"])
    assert not hasattr(reminder, "recipient_ids")


@pytest.mark.parametrize(
    ("dm", "stored"),
    [
        ("Sortir le chien", "⏰ (snooze) Sortir le chien"),
        ("⏰ (snooze) Sortir le chien", "⏰ (snooze) Sortir le chien"),
        ("⏰ (snooze) ⏰ (snooze) Sortir le chien", "⏰ (snooze) Sortir le chien"),
        (None, "⏰ (snooze) Rappel"),
    ],
)
def test_snoozing_marks_the_text_once(dm, stored):
    assert snoozed_text(dm) == stored


def test_a_daily_reminder_keeps_its_hour_across_the_spring_change():
    # Clocks go forward on 29 March 2026: 20:00 stays 20:00, an hour less of UTC between.
    fired = paris(2026, 3, 28, 20, 0)
    after = next_after(fired, "daily", fired)
    assert after == paris(2026, 3, 29, 20, 0)
    assert after.astimezone(LOCAL_TZ).hour == 20
    assert (after - fired).total_seconds() == 23 * 3600


def test_a_daily_reminder_keeps_its_hour_across_the_autumn_change():
    fired = paris(2026, 10, 24, 8, 0)
    after = next_after(fired, "daily", fired)
    assert after.astimezone(LOCAL_TZ).hour == 8
    assert (after - fired).total_seconds() == 25 * 3600
