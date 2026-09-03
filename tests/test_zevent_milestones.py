"""Regression tests for the Zevent donation-milestone announcements.

The first milestone of an edition was silently swallowed: ``last_milestone``
started at ``0`` and ``0`` also meant "nothing seen yet", so the genuine
0 → 100 000 € crossing was mistaken for a restart mid-edition.
"""

import asyncio
from types import SimpleNamespace

import pytest

from extensions.zevent import tasks as module
from extensions.zevent.tasks import TasksMixin

INTERVAL = 100_000


class FakeChannel:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, content):
        self.sent.append(content)
        return SimpleNamespace(id=1)


class Tracker(TasksMixin):
    """The mixin under test, wired the way ``Zevent`` wires it."""

    def __init__(self) -> None:
        self.channel = FakeChannel()
        self.last_milestone: int | None = None
        self._milestone_lock = asyncio.Lock()

    async def _milestone_comparison(self, milestone):
        """No reference edition loaded — the plain announcement stands alone."""
        return None


@pytest.fixture(autouse=True)
def _interval(monkeypatch):
    monkeypatch.setattr(module, "MILESTONE_INTERVAL", INTERVAL)


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
