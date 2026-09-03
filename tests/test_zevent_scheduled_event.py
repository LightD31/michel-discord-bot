"""Behaviour tests for the Zevent Discord scheduled-event sync.

Editing a scheduled event notifies nobody, so the description tracks the live
total — but a cycle with nothing new to say must still make no Discord call at
all. That is not obvious: ``ScheduledEvent.edit()`` leaves the model it edits
untouched, so the fakes here deliberately do the same.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from interactions import ScheduledEventStatus, ScheduledEventType

from extensions.zevent import discord_event as module
from extensions.zevent.api import ApiMixin
from extensions.zevent.discord_event import DiscordEventMixin

GUILD_ID = 809125340280520724
TWITCH_URL = "https://twitch.tv/zevent"
BOT_ID = 999

EVENT_START = datetime(2026, 9, 3, 18, 0, tzinfo=UTC)
MAIN_START = datetime(2026, 9, 4, 16, 0, tzinfo=UTC)
EVENT_END = datetime(2026, 9, 7, 0, 0, tzinfo=UTC)


def payload(total: float) -> dict:
    """A minimal ``zevent.fr`` payload carrying just the donation total."""
    return {"donationAmount": {"number": total, "formatted": f"{total} €"}, "live": []}


class FakeScheduledEvent:
    """Mimics ``interactions.ScheduledEvent``, stale model included."""

    def __init__(self, *, name, description, start_time, end_time, location, creator_id=BOT_ID):
        self.name = name
        self.description = description
        self.start_time = start_time
        self.end_time = end_time
        self.entity_metadata = {"location": location}
        self.entity_type = ScheduledEventType.EXTERNAL
        self.status = ScheduledEventStatus.SCHEDULED
        self.edits: list[dict] = []
        self.deleted = False
        self._creator_id = creator_id

    @property
    async def creator(self):
        return SimpleNamespace(id=self._creator_id)

    async def edit(self, **kwargs):
        # Deliberately does *not* refresh the model — the real one doesn't
        # either, which is exactly what the sync has to cope with.
        self.edits.append(kwargs)

    async def delete(self):
        self.deleted = True


class FakeGuild:
    def __init__(self, events: list[FakeScheduledEvent] | None = None):
        self.events = events or []
        self.created: list[dict] = []

    async def list_scheduled_events(self):
        return list(self.events)

    async def create_scheduled_event(self, **kwargs):
        self.created.append(kwargs)
        event = FakeScheduledEvent(
            name=kwargs["name"],
            description=kwargs["description"],
            start_time=kwargs["start_time"],
            end_time=kwargs["end_time"],
            location=kwargs["external_location"],
        )
        self.events.append(event)
        return event


class FakeClient:
    user = SimpleNamespace(id=BOT_ID)

    def __init__(self, guild: FakeGuild):
        self.guild = guild

    async def fetch_guild(self, guild_id):
        return self.guild


class Tracker(ApiMixin, DiscordEventMixin):
    """The two mixins under test, wired up the way ``Zevent`` wires them."""

    def __init__(self, guild: FakeGuild, event_end: datetime | None = EVENT_END):
        self.guild = guild
        self.client = FakeClient(guild)
        self.message = None
        self._event_title = "ZEvent 2026"
        self._event_start = EVENT_START
        self._main_event_start = MAIN_START
        self._event_end = event_end
        self._scheduled_event = None
        self._applied_plan = None
        self._last_event_total = None
        self._event_finished = False


@pytest.fixture(autouse=True)
def _enable_module(monkeypatch):
    """Turn the feature on: its config is empty outside a configured guild."""
    monkeypatch.setattr(module, "MANAGE_DISCORD_EVENT", True)
    monkeypatch.setattr(module, "TWITCH_URL", TWITCH_URL)
    monkeypatch.setattr(module, "GUILD_ID", GUILD_ID)


@pytest.fixture
def now(monkeypatch):
    """Freeze the clock the sync reads, and let a test move it."""
    clock = {"now": MAIN_START + timedelta(hours=5)}

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"]

    monkeypatch.setattr(module, "datetime", FrozenDatetime)
    return clock


def run(coro):
    return asyncio.run(coro)


# ─── Creation and idle cycles ─────────────────────────────────────────


def test_creates_an_active_event_mid_marathon(now) -> None:
    guild = FakeGuild()
    tracker = Tracker(guild)

    run(tracker.sync_scheduled_event(payload(1_250_000), None, concert_active=False))

    assert len(guild.created) == 1
    created = guild.created[0]
    assert created["name"] == "ZEvent 2026"
    assert created["external_location"] == TWITCH_URL
    assert created["end_time"] == EVENT_END
    # Discord refuses a start in the past, so a running edition starts now.
    assert created["start_time"] == now["now"] + module.START_LEAD
    assert "1 250 000 € récoltés." in created["description"]
    assert tracker._scheduled_event.edits == [{"status": ScheduledEventStatus.ACTIVE}]


def test_an_unchanged_cycle_makes_no_discord_call(now) -> None:
    """The regression this file exists for: no edit when nothing moved.

    ``edit()`` never refreshes the model, so a diff run against the fetched
    object would re-send the same payload on every tick.
    """
    tracker = Tracker(FakeGuild())
    run(tracker.sync_scheduled_event(payload(1_250_000), None, concert_active=False))
    event = tracker._scheduled_event
    event.edits.clear()

    for _ in range(3):
        run(tracker.sync_scheduled_event(payload(1_250_000), None, concert_active=False))

    assert event.edits == []


def test_a_moving_total_is_pushed_once_per_change(now) -> None:
    tracker = Tracker(FakeGuild())
    run(tracker.sync_scheduled_event(payload(1_250_000), None, concert_active=False))
    event = tracker._scheduled_event
    event.edits.clear()

    run(tracker.sync_scheduled_event(payload(1_300_500), None, concert_active=False))
    run(tracker.sync_scheduled_event(payload(1_300_500), None, concert_active=False))

    assert len(event.edits) == 1
    assert "1 300 500 € récoltés." in event.edits[0]["description"]


def test_announced_event_is_started_when_the_edition_opens(now) -> None:
    now["now"] = EVENT_START - timedelta(days=2)
    tracker = Tracker(FakeGuild())
    run(tracker.sync_scheduled_event(payload(0), None, concert_active=False))
    event = tracker._scheduled_event
    assert event.start_time == EVENT_START
    assert event.edits == []

    now["now"] = EVENT_START + timedelta(minutes=1)
    run(tracker.sync_scheduled_event(payload(0), None, concert_active=True))

    assert {"status": ScheduledEventStatus.ACTIVE} in event.edits


def test_an_api_outage_leaves_the_announced_total_alone(now) -> None:
    """A failed fetch is not news: the total only grows, so keep the last one."""
    tracker = Tracker(FakeGuild())
    run(tracker.sync_scheduled_event(payload(1_250_000), None, concert_active=False))
    event = tracker._scheduled_event
    event.edits.clear()

    run(tracker.sync_scheduled_event(None, None, concert_active=False))

    assert event.edits == []


# ─── Ending ───────────────────────────────────────────────────────────


def test_running_event_is_completed_past_the_end(now) -> None:
    """Completed, not deleted — even though the model still reads SCHEDULED."""
    tracker = Tracker(FakeGuild())
    run(tracker.sync_scheduled_event(payload(1_250_000), None, concert_active=False))
    event = tracker._scheduled_event
    assert event.status == ScheduledEventStatus.SCHEDULED  # edit() never refreshes it
    event.edits.clear()

    now["now"] = EVENT_END + timedelta(minutes=1)
    run(tracker.sync_scheduled_event(payload(1_250_000), None, concert_active=False))

    assert event.edits == [{"status": ScheduledEventStatus.COMPLETED}]
    assert event.deleted is False
    assert tracker._scheduled_event is None


def test_announced_event_is_cancelled_when_the_tracker_is_finished(now) -> None:
    now["now"] = EVENT_START - timedelta(days=2)
    tracker = Tracker(FakeGuild())
    run(tracker.sync_scheduled_event(payload(0), None, concert_active=False))
    event = tracker._scheduled_event

    run(tracker.sync_scheduled_event(payload(0), None, concert_active=False, finished=True))

    assert event.deleted is True
    assert tracker._scheduled_event is None


def test_finishing_sticks_across_later_refresh_cycles(now) -> None:
    """The refresh loop keeps running after ``/zevent_finish`` — it must not
    resurrect the event that command just closed."""
    tracker = Tracker(FakeGuild())
    run(tracker.sync_scheduled_event(payload(1_250_000), None, concert_active=False))
    tracker.guild.created.clear()

    run(tracker.sync_scheduled_event(payload(1_250_000), None, concert_active=False, finished=True))
    run(tracker.sync_scheduled_event(payload(1_260_000), None, concert_active=False))

    assert tracker.guild.created == []
    assert tracker._scheduled_event is None


# ─── Recovery ─────────────────────────────────────────────────────────


def test_recovers_the_event_this_bot_created(now) -> None:
    existing = FakeScheduledEvent(
        name="ZEvent 2026",
        description="whatever",
        start_time=EVENT_START,
        end_time=EVENT_END,
        location=TWITCH_URL,
    )
    tracker = Tracker(FakeGuild([existing]))

    run(tracker.recover_scheduled_event())

    assert tracker._scheduled_event is existing
    # No duplicate: the next cycle edits the recovered event instead.
    run(tracker.sync_scheduled_event(payload(1_250_000), None, concert_active=False))
    assert tracker.guild.created == []


def test_ignores_events_from_other_bots_and_other_channels(now) -> None:
    someone_else = FakeScheduledEvent(
        name="ZEvent 2026",
        description="",
        start_time=EVENT_START,
        end_time=EVENT_END,
        location=TWITCH_URL,
        creator_id=1234,
    )
    other_stream = FakeScheduledEvent(
        name="Un stream",
        description="",
        start_time=EVENT_START,
        end_time=EVENT_END,
        location="https://twitch.tv/quelquun",
    )
    tracker = Tracker(FakeGuild([someone_else, other_stream]))

    run(tracker.recover_scheduled_event())

    assert tracker._scheduled_event is None


def test_ignores_an_already_completed_event(now) -> None:
    finished = FakeScheduledEvent(
        name="ZEvent 2025",
        description="",
        start_time=EVENT_START,
        end_time=EVENT_END,
        location=TWITCH_URL,
    )
    finished.status = ScheduledEventStatus.COMPLETED
    tracker = Tracker(FakeGuild([finished]))

    run(tracker.recover_scheduled_event())

    assert tracker._scheduled_event is None


# ─── Opt-out ──────────────────────────────────────────────────────────


def test_does_nothing_when_the_module_is_off(now, monkeypatch) -> None:
    monkeypatch.setattr(module, "MANAGE_DISCORD_EVENT", False)
    tracker = Tracker(FakeGuild())

    run(tracker.sync_scheduled_event(payload(1_250_000), None, concert_active=False))

    assert tracker.guild.created == []


def test_does_nothing_without_a_twitch_channel_to_point_at(now, monkeypatch) -> None:
    """An external Discord event needs a location, and no URL is hardcoded."""
    monkeypatch.setattr(module, "TWITCH_URL", "")
    tracker = Tracker(FakeGuild())

    run(tracker.sync_scheduled_event(payload(1_250_000), None, concert_active=False))

    assert tracker.guild.created == []
