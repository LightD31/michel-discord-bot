"""Zunivers serves every enabled guild, each with its own reminders and event state."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import extensions.zunivers as zunivers
from features.coloc import EventState, ReminderCollection
from features.coloc.constants import ReminderType

GUILDS = {
    "1": {"enabled": True, "colocZuniversChannelId": "10", "journaNormalLink": "https://z/1"},
    "2": {"enabled": True, "colocZuniversChannelId": "20", "journaNormalLink": "https://z/2"},
    "3": {"enabled": False, "colocZuniversChannelId": "30"},
}


class FakeStorage:
    saved: list[tuple[str, str]] = []

    def __init__(self, guild_id):
        self.guild_id = guild_id

    async def load_reminders(self):
        return ReminderCollection()

    async def load_event_state(self):
        return EventState()

    async def save_reminders(self, reminders):
        self.saved.append((self.guild_id, "reminders"))

    async def save_event_state(self, state):
        self.saved.append((self.guild_id, "events"))


class Channel:
    def __init__(self, cid):
        self.id = cid
        self.sent: list = []

    async def send(self, *args, **kwargs):
        self.sent.append(kwargs.get("embed") or kwargs.get("embeds") or args)


@pytest.fixture
def ext(store_config, monkeypatch):
    store_config(
        {
            "config": {"zunivers": {"apiUrl": "https://zunivers.test/api"}},
            "servers": {g: {"moduleZunivers": cfg} for g, cfg in GUILDS.items()},
        }
    )
    FakeStorage.saved = []
    monkeypatch.setattr(zunivers, "StorageManager", FakeStorage)
    channels = {"10": Channel("10"), "20": Channel("20")}
    extension = object.__new__(zunivers.ZuniversExtension)
    extension._states = {}
    extension._states_lock = __import__("asyncio").Lock()
    extension.channels = channels

    async def fetch_channel(cid):
        return channels[str(cid)]

    extension.bot = SimpleNamespace(fetch_channel=fetch_channel)

    async def no_subscriptions(guild_id):
        return {}

    extension._load_request_subscriptions = no_subscriptions
    monkeypatch.setattr(zunivers, "GuildText", Channel)  # the channel type check
    return extension


async def test_each_enabled_guild_gets_its_own_state(ext):
    guilds = await ext._guilds()

    assert [state.guild_id for state, _ in guilds] == ["1", "2"]
    first, second = (state for state, _ in guilds)
    assert first.reminders is not second.reminders
    assert await ext._state("1") is first  # loaded once

    ext._states["9"] = object()  # a guild where the module was switched off
    await ext._guilds()
    assert "9" not in ext._states


async def test_events_are_fetched_once_and_announced_in_each_guild(ext, monkeypatch):
    calls: list[str] = []
    event = {
        "id": "e1",
        "name": "Event",
        "isActive": True,
        "beginDate": "2026-01-01T00:00:00",
        "endDate": "2026-01-02T00:00:00",
    }

    async def get_current_events(rule_set):
        calls.append(rule_set.value)
        return [event] if rule_set == ReminderType.NORMAL else []

    async def get_season():
        calls.append("season")
        return None

    ext.api_client = SimpleNamespace(
        get_current_events=get_current_events, get_current_hardcore_season=get_season
    )
    announced: list[str] = []

    async def fake_notify(channel, event_data, event_type, rule_set):
        announced.append(channel.id)

    ext._send_event_notification = fake_notify

    await ext._check_zunivers_events()

    assert calls == ["NORMAL", "HARDCORE", "season"]
    assert announced == ["10", "20"]
    assert ("1", "events") in FakeStorage.saved and ("2", "events") in FakeStorage.saved

    # The manual check of one guild touches only that guild.
    announced.clear()
    state = await ext._state("2")
    state.event_state.events = {}
    await ext._check_zunivers_events(only="2")
    assert announced == ["20"]


async def test_due_reminders_are_sent_per_guild_with_its_link(ext):
    due = datetime.now() - timedelta(minutes=1)
    (await ext._state("1")).reminders.add_reminder(due, "100", ReminderType.NORMAL)
    (await ext._state("2")).reminders.add_reminder(due, "200", ReminderType.NORMAL)
    processed: list[tuple] = []

    async def fake_process(state, settings, user_id, reminder_type, now):
        processed.append((state.guild_id, user_id, settings["journaNormalLink"]))

    ext._process_reminder = fake_process

    await zunivers.ZuniversExtension.reminder_checker.callback(ext)

    assert processed == [("1", "100", "https://z/1"), ("2", "200", "https://z/2")]
    # Each reminder moves to the next day, in its own guild.
    assert list((await ext._state("1")).reminders.reminders) == [due + timedelta(days=1)]


def test_the_reminder_message_uses_the_guilds_link():
    message = zunivers.ZuniversExtension._build_reminder_message(
        ReminderType.NORMAL, None, None, {"journaNormalLink": "https://z/journa"}
    )
    assert "https://z/journa" in message


async def test_the_247_cycle_is_fetched_once_per_site(ext, monkeypatch):
    import extensions.zunivers.zerator247 as z247

    store = {
        "1": {"enabled": True, "zerator247Url": "https://247.ex"},
        "2": {"enabled": True, "zerator247Url": "https://247.ex"},
        "4": {"enabled": True},  # no site: never polled
    }
    from src.core.config import config_store

    config_store._data = {"servers": {g: {"moduleZunivers": c} for g, c in store.items()}}
    fetched: list[str] = []
    cycle = SimpleNamespace(ends_at=datetime(2026, 1, 1, 12))

    class FakeClient:
        def __init__(self, site):
            self.site = site

        async def get_cycle(self):
            fetched.append(self.site)
            return cycle

    announced: list[str] = []

    async def fake_announce(state, settings, site, c):
        announced.append(state.guild_id)

    monkeypatch.setattr(z247, "Zerator247Client", FakeClient)
    monkeypatch.setattr(z247, "should_notify", lambda c, last: last != c.ends_at)
    ext._announce_requests = fake_announce
    for gid in ("1", "2", "4"):
        (await ext._state(gid)).request_subscriptions = {"u": object()}

    await zunivers.ZuniversExtension.zerator247_checker.callback(ext)
    await zunivers.ZuniversExtension.zerator247_checker.callback(ext)

    assert fetched == ["https://247.ex", "https://247.ex"]  # once per run, not per guild
    assert announced == ["1", "2"]  # each guild announces a cycle once


async def test_without_an_api_url_the_events_check_is_skipped(ext, store_config):
    store_config({"servers": {g: {"moduleZunivers": cfg} for g, cfg in GUILDS.items()}})

    async def unexpected(*args):
        raise AssertionError("the API was called")

    ext.api_client = SimpleNamespace(
        get_current_events=unexpected, get_current_hardcore_season=unexpected
    )

    await ext._check_zunivers_events()

    assert FakeStorage.saved == []
