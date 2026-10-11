"""Tests for the per-user ZUnivers Ninja link opt-in stored with the /journa reminders."""

from datetime import datetime

from features.coloc import ReminderCollection, ReminderType


def test_ninja_link_is_opt_in() -> None:
    reminders = ReminderCollection()
    assert not reminders.wants_ninja_link("1")

    reminders.set_ninja_link("1", True)
    assert reminders.wants_ninja_link("1")

    reminders.set_ninja_link("1", False)
    reminders.set_ninja_link("2", False)  # opting out twice is harmless
    assert not reminders.wants_ninja_link("1")
    assert reminders.ninja_users == set()


def test_ninja_opt_ins_round_trip_next_to_the_schedule() -> None:
    reminders = ReminderCollection()
    reminders.add_reminder(datetime(2026, 10, 11, 8, 0), "1", ReminderType.NORMAL)
    reminders.set_ninja_link("1", True)

    restored = ReminderCollection.from_dict(reminders.to_dict(), sorted(reminders.ninja_users))
    assert restored.reminders == reminders.reminders
    assert restored.wants_ninja_link("1")


def test_legacy_documents_load_without_opt_ins() -> None:
    restored = ReminderCollection.from_dict({"2026-10-11 08:00:00": {"NORMAL": ["1"]}})
    assert restored.get_user_reminders("1") == [(datetime(2026, 10, 11, 8, 0), ReminderType.NORMAL)]
    assert not restored.wants_ninja_link("1")
