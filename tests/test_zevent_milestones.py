"""Regression tests for the Zevent donation-milestone announcements.

The first milestone of an edition was silently swallowed: ``last_milestone``
started at ``0`` and ``0`` also meant "nothing seen yet", so the genuine
0 → 100 000 € crossing was mistaken for a restart mid-edition.

The marker is also persisted per edition, so a reboot resumes where the run
left off instead of silently swallowing whatever was crossed while down.
"""

import asyncio
from types import SimpleNamespace

import pytest

from extensions.zevent import tasks as module
from extensions.zevent.tasks import TasksMixin
from src.core.errors import DatabaseError

INTERVAL = 100_000


class FakeChannel:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, content):
        self.sent.append(content)
        return SimpleNamespace(id=1)


class Tracker(TasksMixin):
    """The mixin under test, wired the way ``Zevent`` wires it."""

    def __init__(self, stats_event: dict | None = None) -> None:
        self.channel = FakeChannel()
        self.last_milestone: int | None = None
        self._milestone_lock = asyncio.Lock()
        self._stats_event = stats_event or {"id": "edition-2026"}

    async def _milestone_comparison(self, milestone):
        """No reference edition loaded — the plain announcement stands alone."""
        return None


class FakeStore:
    """Stands in for the Mongo-backed marker, keyed by edition like the real one."""

    def __init__(self, saved: dict[str | None, int] | None = None) -> None:
        self.saved: dict[str | None, int] = dict(saved or {})
        self.failing = False
        self.error: Exception = DatabaseError("mongo down")

    def __call__(self, guild_id):
        return self

    async def load_milestone(self, event_id):
        if self.failing:
            raise self.error
        return self.saved.get(event_id)

    async def save_milestone(self, event_id, milestone):
        if self.failing:
            raise self.error
        self.saved[event_id] = milestone


@pytest.fixture(autouse=True)
def _interval(monkeypatch):
    monkeypatch.setattr(module, "MILESTONE_INTERVAL", INTERVAL)
    monkeypatch.setattr(module, "GUILD_ID", 809125340280520724)


@pytest.fixture(autouse=True)
def store(monkeypatch):
    """Always stubbed: no test may reach for a real MongoDB."""
    fake = FakeStore()
    monkeypatch.setattr(module, "ZeventStateRepository", fake)
    return fake


def run(coro):
    return asyncio.run(coro)


def test_the_first_milestone_of_an_edition_is_announced() -> None:
    """The reported bug: 93 384 € then 100 501 € sent nothing at all."""
    tracker = Tracker()

    run(tracker.check_and_send_milestone(93_384.62))
    run(tracker.check_and_send_milestone(100_501.17))

    assert tracker.channel.sent == ["🎉 Nouveau palier atteint : 100 000 € récoltés ! 🎉"]


def test_the_first_reading_only_sets_the_baseline() -> None:
    """A restart mid-edition must not replay the milestones already passed."""
    tracker = Tracker()

    run(tracker.check_and_send_milestone(5_243_000))

    assert tracker.channel.sent == []
    assert tracker.last_milestone == 5_200_000


def test_a_restart_still_announces_the_next_milestone() -> None:
    tracker = Tracker()

    run(tracker.check_and_send_milestone(5_243_000))
    run(tracker.check_and_send_milestone(5_301_000))

    assert tracker.channel.sent == ["🎉 Nouveau palier atteint : 5 300 000 € récoltés ! 🎉"]


def test_a_milestone_is_announced_once() -> None:
    tracker = Tracker()

    run(tracker.check_and_send_milestone(93_000))
    for total in (100_501, 120_000, 199_999):
        run(tracker.check_and_send_milestone(total))

    assert len(tracker.channel.sent) == 1


def test_several_milestones_at_once_announce_the_highest() -> None:
    """A gap in the samples reports where the total is now, not every step."""
    tracker = Tracker()

    run(tracker.check_and_send_milestone(10_000))
    run(tracker.check_and_send_milestone(430_000))

    assert tracker.channel.sent == ["🎉 Nouveau palier atteint : 400 000 € récoltés ! 🎉"]


def test_a_total_that_dips_does_not_re_announce() -> None:
    """Zevent and Streamlabs disagree slightly; the higher one wins per cycle."""
    tracker = Tracker()

    run(tracker.check_and_send_milestone(93_000))
    run(tracker.check_and_send_milestone(100_501))
    run(tracker.check_and_send_milestone(99_800))
    run(tracker.check_and_send_milestone(100_600))

    assert len(tracker.channel.sent) == 1


def test_the_comparison_line_is_appended_when_available() -> None:
    tracker = Tracker()

    async def comparison(milestone):
        return "4 h d'avance sur 2025"

    tracker._milestone_comparison = comparison

    run(tracker.check_and_send_milestone(93_000))
    run(tracker.check_and_send_milestone(100_501))

    assert tracker.channel.sent[0].endswith("\n4 h d'avance sur 2025")


# ─── Surviving a reboot ───────────────────────────────────────────────


def test_the_marker_is_persisted_as_it_advances(store) -> None:
    tracker = Tracker()

    run(tracker.check_and_send_milestone(93_000))
    run(tracker.check_and_send_milestone(100_501))

    assert store.saved == {"edition-2026": 100_000}


def test_a_reboot_resumes_from_the_stored_marker(store) -> None:
    """The author's question: a milestone crossed while down is not lost."""
    store.saved["edition-2026"] = 100_000
    tracker = Tracker()

    run(tracker.load_milestone_marker())
    # Back up with the total already past the next palier.
    run(tracker.check_and_send_milestone(205_000))

    assert tracker.channel.sent == ["🎉 Nouveau palier atteint : 200 000 € récoltés ! 🎉"]


def test_a_reboot_does_not_re_announce_the_stored_marker(store) -> None:
    store.saved["edition-2026"] = 200_000
    tracker = Tracker()

    run(tracker.load_milestone_marker())
    run(tracker.check_and_send_milestone(205_000))

    assert tracker.channel.sent == []


def test_a_new_edition_ignores_last_year_s_marker(store) -> None:
    """Otherwise a 16 M € marker would mute the whole next edition."""
    store.saved["edition-2025"] = 16_100_000
    tracker = Tracker(stats_event={"id": "edition-2026"})

    run(tracker.load_milestone_marker())
    run(tracker.check_and_send_milestone(40_000))
    run(tracker.check_and_send_milestone(101_000))

    assert tracker.channel.sent == ["🎉 Nouveau palier atteint : 100 000 € récoltés ! 🎉"]


def test_an_unreachable_database_still_announces(store) -> None:
    """Mongo is not in the path of an announcement — it only survives reboots."""
    store.failing = True
    tracker = Tracker()

    run(tracker.load_milestone_marker())
    run(tracker.check_and_send_milestone(93_000))
    run(tracker.check_and_send_milestone(100_501))

    assert tracker.channel.sent == ["🎉 Nouveau palier atteint : 100 000 € récoltés ! 🎉"]


def test_an_unconfigured_database_still_announces(store) -> None:
    """No `mongodb.url` raises RuntimeError, not DatabaseError, before the
    driver is ever reached — it must not break the refresh cycle either."""
    store.error = RuntimeError("MongoDB URL is not configured")
    store.failing = True
    tracker = Tracker()

    run(tracker.load_milestone_marker())
    run(tracker.check_and_send_milestone(93_000))
    run(tracker.check_and_send_milestone(100_501))

    assert tracker.channel.sent == ["🎉 Nouveau palier atteint : 100 000 € récoltés ! 🎉"]
