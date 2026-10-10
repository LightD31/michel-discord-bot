"""Tests for the ``/bonus`` status derived from the ``/loot/{username}`` history.

The API reports a ``/journa`` as a ``DAILY`` loot and a ``/bonus`` as a
``WEEKLY`` one; ``/bonus`` unlocks once 7 ``/journa`` have been done since
the last one and doesn't stack.
"""

from features.coloc import LootStatus, is_journa_done, journas_since_last_bonus
from features.coloc.constants import (
    ReminderType,
    format_bonus_link,
    get_bonus_reminder_message,
)

LINK = "https://discord.com/channels/138283154589876224/808432657838768168"


def _loot(*entries: tuple[str, str]) -> dict[str, list[dict]]:
    """Build a ``/loot`` payload from ``(timestamp, type)`` pairs."""
    loot: dict[str, list[dict]] = {}
    for timestamp, loot_type in entries:
        loot.setdefault(timestamp[:10], []).append({"type": loot_type, "date": timestamp})
    return loot


def _dailies(start_day: int, count: int) -> list[tuple[str, str]]:
    return [
        (f"2026-10-{day:02d}T08:00:00.000000", "DAILY")
        for day in range(start_day, start_day + count)
    ]


def test_journas_since_last_bonus_counts_after_the_latest_bonus():
    loot = _loot(*_dailies(1, 3), ("2026-10-03T08:00:05.000000", "WEEKLY"), *_dailies(4, 5))
    assert journas_since_last_bonus(loot) == 5


def test_bonus_claimed_right_after_the_unlocking_journa_resets_the_count():
    # Same day as the 7th /journa, a few seconds later: the timestamp decides the order.
    loot = _loot(*_dailies(1, 7), ("2026-10-07T08:00:05.000000", "WEEKLY"))
    assert journas_since_last_bonus(loot) == 0


def test_journas_since_last_bonus_without_any_bonus_counts_everything():
    assert journas_since_last_bonus(_loot(*_dailies(1, 4))) == 4
    assert journas_since_last_bonus({}) == 0


def test_is_journa_done_ignores_a_bonus_alone_on_that_day():
    loot = _loot(*_dailies(1, 7), ("2026-10-08T09:00:00.000000", "WEEKLY"))
    assert is_journa_done(loot, "2026-10-07")
    assert not is_journa_done(loot, "2026-10-08")
    assert not is_journa_done(loot, "2026-10-09")


def test_loot_status_bonus_available_after_seven_journas():
    status = LootStatus.from_loot(_loot(*_dailies(1, 7)), "2026-10-07")
    assert status.journa_done
    assert status.bonus_available
    assert not status.bonus_unlocked_by_next_journa


def test_loot_status_next_journa_unlocks_the_bonus():
    status = LootStatus.from_loot(_loot(*_dailies(1, 6)), "2026-10-07")
    assert not status.journa_done
    assert not status.bonus_available
    assert status.bonus_unlocked_by_next_journa


def test_loot_status_nothing_pending():
    status = LootStatus.from_loot(_loot(*_dailies(1, 3)), "2026-10-03")
    assert status.journa_done
    assert not status.bonus_available
    assert not status.bonus_unlocked_by_next_journa


def test_format_bonus_link():
    assert format_bonus_link(LINK) == f"[/bonus]({LINK})"
    assert format_bonus_link(None) == "`/bonus`"


def test_bonus_reminder_templates_render_with_the_link():
    for reminder_type in (ReminderType.NORMAL, ReminderType.HARDCORE):
        for template in get_bonus_reminder_message(reminder_type):
            rendered = template.format(bonus=format_bonus_link(LINK))
            assert LINK in rendered
            assert "{bonus}" not in rendered
