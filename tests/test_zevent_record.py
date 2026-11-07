"""Tests for the Zevent "record battu" announcement.

The record is a once-per-edition headline, so it is guarded harder than a
palier: it goes out exactly once, only to a tracker that actually watched the
previous edition's total fall, and it survives a reboot in both directions —
a crossing that happened while the bot was down is still announced, while one
already announced is never repeated.
"""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from extensions.zevent import tasks as module
from extensions.zevent._common import ZeventGuild
from extensions.zevent.tasks import TasksMixin
from src.core.errors import DatabaseError

THIS_START = datetime(2026, 9, 4, 16, 0, tzinfo=UTC)

# What ZEvent 2025 actually raised, per the API listing — the record 2026 has
# to beat, and the figure the tracker compares against.
RECORD = 16_658_660.0


class FakeChannel:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, content):
        self.sent.append(content)
        return SimpleNamespace(id=1)


class Tracker(TasksMixin):
    """The mixin under test, wired the way ``Zevent`` wires it."""

    def __init__(self, record=("2025", RECORD), stats_event: dict | None = None) -> None:
        self.channel = FakeChannel()
        # One guild's tracker state, its message posted in ``channel``.
        self.guild = ZeventGuild(
            "809125340280520724", message=SimpleNamespace(channel=self.channel)
        )
        self._main_event_start = THIS_START
        self._stats_event = stats_event or {"id": "edition-2026"}
        self._record = record

    async def reference_record(self):
        return self._record


class FakeStore:
    """Stands in for the Mongo-backed marker, keyed by edition like the real one."""

    def __init__(self) -> None:
        self.saved: dict[str | None, bool] = {}
        self.failing = False
        self.error: Exception = DatabaseError("mongo down")

    def __call__(self, guild_id):
        return self

    async def load_record(self, event_id):
        if self.failing:
            raise self.error
        return self.saved.get(event_id)

    async def save_record(self, event_id, announced):
        if self.failing:
            raise self.error
        self.saved[event_id] = announced


@pytest.fixture(autouse=True)
def store(monkeypatch):
    """Always stubbed: no test may reach for a real MongoDB."""
    fake = FakeStore()
    monkeypatch.setattr(module, "ZeventStateRepository", fake)
    return fake


def run(coro):
    return asyncio.run(coro)


def test_the_record_falling_is_announced() -> None:
    tracker = Tracker()

    run(tracker.check_and_send_record(tracker.guild, 16_600_000))
    run(tracker.check_and_send_record(tracker.guild, 16_700_797))

    assert len(tracker.channel.sent) == 1
    message = tracker.channel.sent[0]
    assert "Record battu" in message
    assert "16 658 660 €" in message
    assert "16 700 797 €" in message
    assert "2025" in message


def test_the_record_is_announced_once() -> None:
    tracker = Tracker()

    run(tracker.check_and_send_record(tracker.guild, 16_600_000))
    for total in (16_700_797, 16_900_000, 17_000_000):
        run(tracker.check_and_send_record(tracker.guild, total))

    assert len(tracker.channel.sent) == 1


def test_nothing_is_said_while_the_record_still_stands() -> None:
    tracker = Tracker()

    run(tracker.check_and_send_record(tracker.guild, 0))
    run(tracker.check_and_send_record(tracker.guild, 9_000_000))
    run(tracker.check_and_send_record(tracker.guild, 16_658_659))

    assert tracker.channel.sent == []


def test_an_edition_first_read_from_above_the_record_says_nothing() -> None:
    """A tracker configured mid-marathon never watched the record fall."""
    tracker = Tracker()

    run(tracker.check_and_send_record(tracker.guild, 16_800_000))
    run(tracker.check_and_send_record(tracker.guild, 16_900_000))

    assert tracker.channel.sent == []


def test_no_comparable_edition_means_no_record_to_beat() -> None:
    tracker = Tracker(record=None)

    run(tracker.check_and_send_record(tracker.guild, 16_700_797))

    assert tracker.channel.sent == []
    # Nothing was decided about this edition, so a listing arriving later still
    # gets its chance rather than finding the marker already written.
    assert tracker.guild.record_state is None


def test_a_total_that_dips_back_does_not_re_announce() -> None:
    """Zevent and Streamlabs disagree slightly; the higher one wins per cycle."""
    tracker = Tracker()

    run(tracker.check_and_send_record(tracker.guild, 16_600_000))
    run(tracker.check_and_send_record(tracker.guild, 16_700_797))
    run(tracker.check_and_send_record(tracker.guild, 16_650_000))
    run(tracker.check_and_send_record(tracker.guild, 16_710_000))

    assert len(tracker.channel.sent) == 1


def test_an_unusable_channel_leaves_the_announcement_pending() -> None:
    """Unlike a palier, this one never comes round again."""
    tracker = Tracker()
    run(tracker.check_and_send_record(tracker.guild, 16_600_000))
    tracker.guild.message = None  # its channel gone

    run(tracker.check_and_send_record(tracker.guild, 16_700_797))
    tracker.channel = FakeChannel()
    tracker.guild.message = SimpleNamespace(channel=tracker.channel)
    run(tracker.check_and_send_record(tracker.guild, 16_710_000))

    assert len(tracker.channel.sent) == 1


# ─── Surviving a reboot ───────────────────────────────────────────────


def test_watching_below_the_record_is_persisted(store) -> None:
    tracker = Tracker()

    run(tracker.check_and_send_record(tracker.guild, 16_600_000))

    assert store.saved == {"edition-2026": False}


def test_the_announcement_is_persisted(store) -> None:
    tracker = Tracker()

    run(tracker.check_and_send_record(tracker.guild, 16_600_000))
    run(tracker.check_and_send_record(tracker.guild, 16_700_797))

    assert store.saved == {"edition-2026": True}


def test_a_record_broken_while_the_bot_was_down_is_still_announced(store) -> None:
    """The stored `False` is proof this tracker saw the record still standing."""
    store.saved["edition-2026"] = False
    tracker = Tracker()

    run(tracker.load_record_marker(tracker.guild))
    run(tracker.check_and_send_record(tracker.guild, 16_800_000))

    assert len(tracker.channel.sent) == 1


def test_a_reboot_does_not_re_announce_a_record_already_broken(store) -> None:
    store.saved["edition-2026"] = True
    tracker = Tracker()

    run(tracker.load_record_marker(tracker.guild))
    run(tracker.check_and_send_record(tracker.guild, 16_800_000))

    assert tracker.channel.sent == []


def test_a_new_edition_ignores_last_year_s_marker(store) -> None:
    """Otherwise the 2026 record would be announced as already broken."""
    store.saved["edition-2025"] = True
    tracker = Tracker(stats_event={"id": "edition-2026"})

    run(tracker.load_record_marker(tracker.guild))
    run(tracker.check_and_send_record(tracker.guild, 1_000_000))
    run(tracker.check_and_send_record(tracker.guild, 16_800_000))

    assert len(tracker.channel.sent) == 1


def test_an_unreachable_database_still_announces(store) -> None:
    """Mongo is not in the path of an announcement — it only survives reboots."""
    store.failing = True
    tracker = Tracker()

    run(tracker.load_record_marker(tracker.guild))
    run(tracker.check_and_send_record(tracker.guild, 16_600_000))
    run(tracker.check_and_send_record(tracker.guild, 16_700_797))

    assert len(tracker.channel.sent) == 1


def test_an_unconfigured_database_still_announces(store) -> None:
    """No `mongodb.url` raises RuntimeError, not DatabaseError, before the
    driver is ever reached — it must not break the refresh cycle either."""
    store.error = RuntimeError("MongoDB URL is not configured")
    store.failing = True
    tracker = Tracker()

    run(tracker.load_record_marker(tracker.guild))
    run(tracker.check_and_send_record(tracker.guild, 16_600_000))
    run(tracker.check_and_send_record(tracker.guild, 16_700_797))

    assert len(tracker.channel.sent) == 1
