"""When a reminder fires: first occurrence, catch-up after downtime, recipients, labels."""

from datetime import date, datetime

import pytest

from features.reminders import (
    PastReminderError,
    Reminder,
    first_occurrence,
    next_after,
    parse_recipients,
    schedule_label,
    snooze_minutes,
)

NOW = datetime(2026, 3, 10, 12, 0)  # a Tuesday


def test_a_time_still_ahead_today_fires_today():
    assert first_occurrence(NOW, hour=18, minute=30, frequency=None) == datetime(
        2026, 3, 10, 18, 30
    )


def test_a_time_already_past_today_fires_tomorrow():
    assert first_occurrence(NOW, hour=8, minute=0, frequency=None) == datetime(2026, 3, 11, 8, 0)
    assert first_occurrence(NOW, hour=12, minute=0, frequency=None) == datetime(2026, 3, 11, 12, 0)


def test_a_one_shot_reminder_on_a_past_date_is_refused():
    with pytest.raises(PastReminderError):
        first_occurrence(NOW, hour=23, minute=0, frequency=None, start=date(2026, 3, 9))
    with pytest.raises(PastReminderError):  # today, but the time has passed
        first_occurrence(NOW, hour=9, minute=0, frequency=None, start=date(2026, 3, 10))


def test_a_one_shot_reminder_on_a_future_date_keeps_it():
    assert first_occurrence(
        NOW, hour=9, minute=15, frequency=None, start=date(2026, 4, 1)
    ) == datetime(2026, 4, 1, 9, 15)


@pytest.mark.parametrize(
    ("frequency", "expected"),
    [
        ("daily", datetime(2026, 3, 11, 9, 0)),
        ("weekly", datetime(2026, 3, 16, 9, 0)),
        ("monthly", datetime(2026, 4, 2, 9, 0)),
        ("yearly", datetime(2027, 3, 2, 9, 0)),
    ],
)
def test_a_recurring_reminder_starting_in_the_past_fires_at_its_next_occurrence(
    frequency, expected
):
    start = date(2026, 3, 2)  # the Monday before NOW
    assert first_occurrence(NOW, hour=9, minute=0, frequency=frequency, start=start) == expected


def test_next_after_skips_occurrences_missed_while_the_bot_was_down():
    fired = datetime(2026, 3, 1, 9, 0)
    assert next_after(fired, "daily", NOW) == datetime(2026, 3, 11, 9, 0)
    assert next_after(fired, "weekly", NOW) == datetime(2026, 3, 15, 9, 0)
    assert next_after(fired, None, NOW) is None


def test_next_after_steps_one_period_when_on_time():
    fired = datetime(2026, 3, 10, 11, 59)
    assert next_after(fired, "monthly", NOW) == datetime(2026, 4, 10, 11, 59)
    assert next_after(datetime(2024, 2, 29, 8, 0), "yearly", NOW) == datetime(2027, 2, 28, 8, 0)


def test_recipients_accept_mentions_and_ids_without_duplicates():
    assert parse_recipients("<@1>, <@!2>;3 1  2") == ["1", "2", "3"]
    assert parse_recipients(None) == []
    assert parse_recipients("") == []


@pytest.mark.parametrize("raw", ["alice", "<@&42>", "12a", "<#5>"])
def test_recipients_reject_anything_but_user_ids(raw):
    assert parse_recipients(raw) is None


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
        user_id="1", message="m", remind_time=datetime(2026, 3, 10, 8, 5), frequency=frequency
    )
    assert schedule_label(reminder) == label


def test_all_recipients_always_include_the_author_once():
    reminder = Reminder(user_id="1", message="m", remind_time=NOW, recipient_ids=["2", "1", "3"])
    assert reminder.all_recipients() == ["1", "2", "3"]
    assert Reminder(user_id="1", message="m", remind_time=NOW).all_recipients() == ["1"]
