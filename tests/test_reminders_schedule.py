"""When a reminder fires: first occurrence, catch-up after downtime, labels."""

from datetime import date, datetime

import pytest

from features.reminders import (
    PastReminderError,
    Reminder,
    first_occurrence,
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


def test_a_time_still_ahead_today_fires_today():
    assert first_occurrence(NOW, hour=18, minute=30, frequency=None) == paris(2026, 3, 10, 18, 30)


def test_a_time_already_past_today_fires_tomorrow():
    assert first_occurrence(NOW, hour=8, minute=0, frequency=None) == paris(2026, 3, 11, 8, 0)
    assert first_occurrence(NOW, hour=12, minute=0, frequency=None) == paris(2026, 3, 11, 12, 0)


def test_a_one_shot_reminder_on_a_past_date_is_refused():
    with pytest.raises(PastReminderError):
        first_occurrence(NOW, hour=23, minute=0, frequency=None, start=date(2026, 3, 9))
    with pytest.raises(PastReminderError):  # today, but the time has passed
        first_occurrence(NOW, hour=9, minute=0, frequency=None, start=date(2026, 3, 10))


def test_a_one_shot_reminder_on_a_future_date_keeps_it():
    assert first_occurrence(
        NOW, hour=9, minute=15, frequency=None, start=date(2026, 4, 1)
    ) == paris(2026, 4, 1, 9, 15)


@pytest.mark.parametrize(
    ("frequency", "expected"),
    [
        ("daily", paris(2026, 3, 11, 9, 0)),
        ("weekly", paris(2026, 3, 16, 9, 0)),
        ("monthly", paris(2026, 4, 2, 9, 0)),
        ("yearly", paris(2027, 3, 2, 9, 0)),
    ],
)
def test_a_recurring_reminder_starting_in_the_past_fires_at_its_next_occurrence(
    frequency, expected
):
    start = date(2026, 3, 2)  # the Monday before NOW
    assert first_occurrence(NOW, hour=9, minute=0, frequency=frequency, start=start) == expected


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


def test_occurrences_are_utc_instants():
    when = first_occurrence(NOW, hour=18, minute=30, frequency=None)
    assert when.utcoffset().total_seconds() == 0
    assert when == datetime(2026, 3, 10, 17, 30, tzinfo=when.tzinfo)  # 18:30 in Paris, winter


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


def test_a_weekly_reminder_set_before_the_change_fires_at_the_same_wall_time():
    start = date(2026, 3, 23)  # a Monday, before the change
    now = paris(2026, 3, 31, 12, 0)
    when = first_occurrence(now, hour=9, minute=0, frequency="weekly", start=start)
    assert when == paris(2026, 4, 6, 9, 0)


def test_a_time_skipped_by_the_spring_change_lands_an_hour_later():
    now = paris(2026, 3, 28, 12, 0)
    when = first_occurrence(now, hour=2, minute=30, frequency=None, start=date(2026, 3, 29))
    assert when.astimezone(LOCAL_TZ).hour == 3
