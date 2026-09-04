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
WEBSITE_URL = "https://zevent.fr"
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
        self._applied_cover_url = None
        self._last_event_total = None
        self._event_finished = False


@pytest.fixture(autouse=True)
def _enable_module(monkeypatch):
    """Turn the feature on: its config is empty outside a configured guild."""
    monkeypatch.setattr(module, "MANAGE_DISCORD_EVENT", True)
    monkeypatch.setattr(module, "TWITCH_URL", TWITCH_URL)
    monkeypatch.setattr(module, "WEBSITE_URL", WEBSITE_URL)
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
    assert created["name"] == "ZEvent 2026 - 1 250 000 €"
    assert created["external_location"] == WEBSITE_URL
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
    """Name and description move together, in a single edit."""
    tracker = Tracker(FakeGuild())
    run(tracker.sync_scheduled_event(payload(1_250_000), None, concert_active=False))
    event = tracker._scheduled_event
    event.edits.clear()

    run(tracker.sync_scheduled_event(payload(1_300_500), None, concert_active=False))
    run(tracker.sync_scheduled_event(payload(1_300_500), None, concert_active=False))

    assert len(event.edits) == 1
    assert event.edits[0]["name"] == "ZEvent 2026 - 1 300 500 €"
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


# ─── Location ─────────────────────────────────────────────────────────


def test_the_concert_points_at_the_twitch_channel(now) -> None:
    """One channel carries the opening concert, so that is where to send people."""
    now["now"] = EVENT_START + timedelta(hours=1)
    tracker = Tracker(FakeGuild())

    run(tracker.sync_scheduled_event(payload(0), None, concert_active=True))

    assert tracker.guild.created[0]["external_location"] == TWITCH_URL


def test_the_location_moves_to_the_site_when_the_marathon_starts(now) -> None:
    """From then on every participant streams on their own channel."""
    now["now"] = EVENT_START + timedelta(hours=1)
    tracker = Tracker(FakeGuild())
    run(tracker.sync_scheduled_event(payload(0), None, concert_active=True))
    event = tracker._scheduled_event
    event.edits.clear()

    now["now"] = MAIN_START + timedelta(minutes=1)
    run(tracker.sync_scheduled_event(payload(120_000), None, concert_active=False))

    assert event.edits[0]["external_location"] == WEBSITE_URL


def test_the_location_is_not_rewritten_every_cycle(now) -> None:
    tracker = Tracker(FakeGuild())
    run(tracker.sync_scheduled_event(payload(1_250_000), None, concert_active=False))
    event = tracker._scheduled_event
    event.edits.clear()

    run(tracker.sync_scheduled_event(payload(1_250_000), None, concert_active=False))

    assert event.edits == []


def test_an_event_created_during_the_concert_is_still_recovered(now) -> None:
    """Recovery must accept either location, or the loop creates a duplicate."""
    existing = FakeScheduledEvent(
        name="ZEvent 2026",
        description="",
        start_time=EVENT_START,
        end_time=EVENT_END,
        location=TWITCH_URL,
    )
    tracker = Tracker(FakeGuild([existing]))

    run(tracker.recover_scheduled_event())

    assert tracker._scheduled_event is existing
    run(tracker.sync_scheduled_event(payload(1_250_000), None, concert_active=False))
    assert tracker.guild.created == []
    assert existing.edits[0]["external_location"] == WEBSITE_URL


def test_only_a_twitch_channel_configured_keeps_using_it(now, monkeypatch) -> None:
    monkeypatch.setattr(module, "WEBSITE_URL", "")
    tracker = Tracker(FakeGuild())

    run(tracker.sync_scheduled_event(payload(1_250_000), None, concert_active=False))

    assert tracker.guild.created[0]["external_location"] == TWITCH_URL


def test_only_a_site_configured_uses_it_throughout(now, monkeypatch) -> None:
    monkeypatch.setattr(module, "TWITCH_URL", "")
    now["now"] = EVENT_START + timedelta(hours=1)
    tracker = Tracker(FakeGuild())

    run(tracker.sync_scheduled_event(payload(0), None, concert_active=True))

    assert tracker.guild.created[0]["external_location"] == WEBSITE_URL


def test_neither_url_configured_disables_the_event(now, monkeypatch) -> None:
    monkeypatch.setattr(module, "TWITCH_URL", "")
    monkeypatch.setattr(module, "WEBSITE_URL", "")
    tracker = Tracker(FakeGuild())

    run(tracker.sync_scheduled_event(payload(1_250_000), None, concert_active=False))

    assert tracker.guild.created == []


# ─── Cover image ──────────────────────────────────────────────────────


@pytest.fixture
def cover(monkeypatch):
    """Serve a configured cover, counting how often it is downloaded."""
    state = {"downloads": 0, "status": 200, "body": b"PNG-bytes"}
    monkeypatch.setattr(module, "EVENT_COVER_URL", "https://example.invalid/cover.png")

    class FakeResponse:
        def __init__(self):
            self.status = state["status"]

        async def read(self):
            return state["body"]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class FakeSession:
        def get(self, url):
            state["downloads"] += 1
            return FakeResponse()

    async def fake_session():
        return FakeSession()

    monkeypatch.setattr(module.http_client, "session", fake_session)
    return state


def test_the_cover_is_uploaded_with_the_new_event(now, cover) -> None:
    tracker = Tracker(FakeGuild())

    run(tracker.sync_scheduled_event(payload(1_250_000), None, concert_active=False))

    assert tracker.guild.created[0]["cover_image"] == b"PNG-bytes"
    assert cover["downloads"] == 1


def test_the_cover_is_not_re_uploaded_on_later_cycles(now, cover) -> None:
    """It is decoration, not state: fetching it once per process is enough."""
    tracker = Tracker(FakeGuild())
    run(tracker.sync_scheduled_event(payload(1_250_000), None, concert_active=False))
    event = tracker._scheduled_event

    run(tracker.sync_scheduled_event(payload(1_300_000), None, concert_active=False))

    assert cover["downloads"] == 1
    assert "cover_image" not in event.edits[0]


def test_a_recovered_event_gets_the_cover_on_the_next_cycle(now, cover) -> None:
    existing = FakeScheduledEvent(
        name="ZEvent 2026",
        description="",
        start_time=EVENT_START,
        end_time=EVENT_END,
        location=TWITCH_URL,
    )
    tracker = Tracker(FakeGuild([existing]))
    run(tracker.recover_scheduled_event())

    run(tracker.sync_scheduled_event(payload(1_250_000), None, concert_active=False))

    assert existing.edits[0]["cover_image"] == b"PNG-bytes"


def test_an_unreachable_cover_is_attempted_once_and_dropped(now, cover) -> None:
    """A 404 must not cost a download per refresh — the event ships without it."""
    cover["status"] = 404
    tracker = Tracker(FakeGuild())

    run(tracker.sync_scheduled_event(payload(1_250_000), None, concert_active=False))
    run(tracker.sync_scheduled_event(payload(1_300_000), None, concert_active=False))

    assert tracker.guild.created[0]["cover_image"] is None
    assert cover["downloads"] == 1


def test_an_oversized_cover_is_refused(now, cover) -> None:
    cover["body"] = b"x" * (module.MAX_COVER_BYTES + 1)
    tracker = Tracker(FakeGuild())

    run(tracker.sync_scheduled_event(payload(1_250_000), None, concert_active=False))

    assert tracker.guild.created[0]["cover_image"] is None


def test_no_cover_configured_means_no_upload(now) -> None:
    tracker = Tracker(FakeGuild())

    run(tracker.sync_scheduled_event(payload(1_250_000), None, concert_active=False))

    assert tracker.guild.created[0]["cover_image"] is None


# ─── Opt-out ──────────────────────────────────────────────────────────


def test_does_nothing_when_the_module_is_off(now, monkeypatch) -> None:
    monkeypatch.setattr(module, "MANAGE_DISCORD_EVENT", False)
    tracker = Tracker(FakeGuild())

    run(tracker.sync_scheduled_event(payload(1_250_000), None, concert_active=False))

    assert tracker.guild.created == []
