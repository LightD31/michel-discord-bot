"""Tests for the per-user ZUnivers Ninja preferences stored with the /journa reminders."""

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

    reminders.set_ninja_strategy("1", "prudent")

    restored = ReminderCollection.from_dict(
        reminders.to_dict(), sorted(reminders.ninja_users), reminders.ninja_strategies
    )
    assert restored.reminders == reminders.reminders
    assert restored.wants_ninja_link("1")
    assert restored.ninja_strategy("1") == "prudent"


def test_ninja_strategy_set_and_cleared() -> None:
    reminders = ReminderCollection()
    assert reminders.ninja_strategy("1") is None

    reminders.set_ninja_strategy("1", "vortex")
    assert reminders.ninja_strategy("1") == "vortex"

    reminders.set_ninja_strategy("1", None)
    reminders.set_ninja_strategy("2", None)  # clearing twice is harmless
    assert reminders.ninja_strategy("1") is None
    assert reminders.ninja_strategies == {}


def test_legacy_documents_load_without_opt_ins() -> None:
    restored = ReminderCollection.from_dict({"2026-10-11 08:00:00": {"NORMAL": ["1"]}})
    assert restored.get_user_reminders("1") == [(datetime(2026, 10, 11, 8, 0), ReminderType.NORMAL)]
    assert not restored.wants_ninja_link("1")
    assert restored.ninja_strategy("1") is None
