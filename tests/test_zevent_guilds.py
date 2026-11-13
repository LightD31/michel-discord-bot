"""Zevent tracks one edition for the whole bot and publishes it in every enabled guild.

The edition (its URLs, dates, display knobs) is the global ``zevent`` section;
each guild keeps only its channel, its pin, its milestone and record markers,
and its own Discord scheduled event.
"""

from types import SimpleNamespace

import pytest

import extensions.zevent.commands as commands
import extensions.zevent.tasks as tasks
from extensions.zevent import Zevent
from extensions.zevent._common import ZeventGuild


class FakeMessages:
    """Stands in for GuildMessages: one fake message per guild with a channel."""

    def __init__(self) -> None:
        self.kept: list[str] | None = None
        self.asked: list[str] = []

    async def get(self, guild_id, settings):
        self.asked.append(str(guild_id))
        if not settings.get("zeventChannelId"):
            return None
        return SimpleNamespace(name=f"message-{guild_id}", channel=None)

    def keep_only(self, guild_ids):
        self.kept = list(guild_ids)


def _servers(**guilds: dict) -> dict:
    return {"servers": {gid: {"moduleZevent": cfg} for gid, cfg in guilds.items()}}


@pytest.fixture
def ext(store_config):
    store_config(
        _servers(
            **{
                "1": {"enabled": True, "zeventChannelId": "10"},
                "2": {"enabled": True, "zeventChannelId": "20", "zeventManageDiscordEvent": True},
                "3": {"enabled": False, "zeventChannelId": "30"},
            }
        )
    )
    extension = object.__new__(Zevent)
    extension.messages = FakeMessages()
    extension.guilds = {}
    return extension


async def test_each_enabled_guild_gets_its_state_once(ext):
    loaded: list[tuple[str, str]] = []

    async def load_milestone(guild):
        loaded.append(("milestone", guild.guild_id))

    async def load_record(guild):
        loaded.append(("record", guild.guild_id))

    async def recover(guild, settings):
        loaded.append(("event", guild.guild_id, bool(settings.get("zeventManageDiscordEvent"))))

    ext.load_milestone_marker = load_milestone
    ext.load_record_marker = load_record
    ext.recover_scheduled_event = recover
    ext.guilds["9"] = ZeventGuild("9")  # a guild where the module was switched off

    first = await ext._guilds()
    second = await ext._guilds()

    assert [guild.guild_id for guild, _ in first] == ["1", "2"]
    assert [guild for guild, _ in second] == [guild for guild, _ in first]  # kept
    assert set(ext.guilds) == {"1", "2"}
    assert ext.messages.kept == ["1", "2"]
    assert first[0][0].message.name == "message-1"
    # Markers are picked back up once per guild, the scheduled event once per
    # guild that asks for one.
    assert loaded == [
        ("milestone", "1"),
        ("record", "1"),
        ("milestone", "2"),
        ("record", "2"),
        ("event", "2", True),
    ]

    # Asking for the event later still re-attaches the one a previous run left.
    from src.core.config import config_store

    config_store._data["servers"]["1"]["moduleZevent"]["zeventManageDiscordEvent"] = True
    loaded.clear()
    await ext._guilds()
    await ext._guilds()
    assert loaded == [("event", "1", True)]


async def test_the_refresh_renders_once_and_publishes_in_each_guild(ext, monkeypatch):
    broken = ZeventGuild("1", message="broken")
    fine = ZeventGuild("2", message="message-2")
    quiet = ZeventGuild("4")  # no channel configured: no message to edit
    guilds = [(broken, {}), (fine, {"zeventManageDiscordEvent": True}), (quiet, {})]

    async def fake_guilds():
        return guilds

    async def fake_fetch(url, return_type):
        return {"live": []}

    async def nothing(*args, **kwargs):
        return None

    async def no_concert():
        return False

    async def streams(live):
        return {"LAN": [], "Online": []}

    async def viewers(live):
        return {"Total": "1", "LAN": "0", "Online": "1"}

    rendered: list[str] = []

    def render(data, planning, main, streams, viewers=None):
        rendered.append(main)
        return ["embeds"]

    synced: list[str] = []
    announced: list[tuple] = []
    edited: list[str] = []

    async def sync(guild, settings, data, streamlabs, concert_active):
        synced.append(guild.guild_id)

    async def milestone(guild, total):
        announced.append(("milestone", guild.guild_id, total))

    async def record(guild, total):
        announced.append(("record", guild.guild_id, total))

    async def fake_edit(message, **kwargs):
        if message == "broken":
            raise RuntimeError("Discord refused the edit")
        edited.append(message)
        assert kwargs["embeds"] == ["embeds"]

    monkeypatch.setattr(tasks, "fetch", fake_fetch)
    monkeypatch.setattr(tasks, "API_URL", "https://zevent.test/api/")
    monkeypatch.setattr(tasks, "STREAMLABS_API_URL", "")
    monkeypatch.setattr(tasks, "edit_message_if_changed", fake_edit)
    ext._guilds = fake_guilds
    ext._ensure_planning_cache = nothing
    ext._validate_api_data = lambda data, source: True
    ext.record_donation_sample = lambda live: None
    ext._is_concert_active = no_concert
    ext._is_event_started = lambda: True
    ext._is_main_event_started = lambda: True
    ext.last_data_cache = None
    ext.get_total_amount = lambda data, streamlabs: ("150 000 €", 150_000)
    ext.categorize_streams = streams
    ext.get_viewers_by_location = viewers
    ext.create_main_embed = lambda total, viewers=None: f"main-{total}"
    ext._tracker_embeds = render
    ext.sync_scheduled_event = sync
    ext.check_and_send_milestone = milestone
    ext.check_and_send_record = record

    await Zevent.zevent.callback(ext)

    assert rendered == ["main-150 000 €"]  # one render for every guild
    assert edited == ["message-2"]  # the broken guild did not stop the others
    assert synced == ["1", "2", "4"]
    assert announced == [
        ("milestone", "1", 150_000),
        ("record", "1", 150_000),
        ("milestone", "2", 150_000),
        ("record", "2", 150_000),
        ("milestone", "4", 150_000),
        ("record", "4", 150_000),
    ]


async def test_the_refresh_does_nothing_without_an_enabled_guild(store_config, monkeypatch):
    store_config(_servers(**{"1": {"enabled": False}}))
    ext = object.__new__(Zevent)
    ext.messages = FakeMessages()
    ext.guilds = {}

    async def must_not_fetch(*args, **kwargs):
        raise AssertionError("fetched while the module is off everywhere")

    monkeypatch.setattr(tasks, "fetch", must_not_fetch)
    await Zevent.zevent.callback(ext)


class FakeCtx:
    def __init__(self, guild_id):
        self.guild_id = guild_id
        self.replies: list[str] = []
        # An organiser: /zevent_finish needs « Gérer le serveur ».
        self.author = SimpleNamespace(has_permission=lambda *perms: True)

    async def send(self, content, ephemeral=False):
        self.replies.append(content)


@pytest.fixture
def finishing(ext, monkeypatch):
    async def fake_fetch(url, return_type):
        return {"live": []}

    async def streams(live):
        return {"LAN": [], "Online": []}

    calls: dict[str, list] = {"synced": [], "edited": []}

    async def sync(guild, settings, data, streamlabs, concert_active, finished):
        calls["synced"].append((guild.guild_id, finished))

    async def fake_edit(message, **kwargs):
        calls["edited"].append(message.name)

    monkeypatch.setattr(commands, "fetch", fake_fetch)
    monkeypatch.setattr(commands, "API_URL", "https://zevent.test/api/")
    monkeypatch.setattr(commands, "edit_message_if_changed", fake_edit)
    ext._validate_api_data = lambda data, source: True
    ext._safe_get_data = lambda data, path, default: default
    ext.categorize_streams = streams
    ext._get_stream_total_count = lambda streams, location: 0
    ext.create_main_embed = lambda total, finished=False: "main"
    ext.create_location_embed = lambda *args, **kwargs: "location"
    ext.ensure_embeds_fit_limit = lambda embeds: embeds
    ext.sync_scheduled_event = sync
    return calls


async def test_finishing_closes_only_the_guild_it_is_run_in(ext, finishing):
    ctx = FakeCtx(2)

    await Zevent.end.callback(ext, ctx)

    assert finishing["synced"] == [("2", True)]
    assert finishing["edited"] == ["message-2"]
    assert ext.guilds["2"].guild_id == "2"
    assert ctx.replies == ["Embed final créé avec succès"]


async def test_finishing_is_refused_where_the_module_is_off(ext, finishing):
    ctx = FakeCtx(3)

    await Zevent.end.callback(ext, ctx)

    assert finishing["synced"] == [] and finishing["edited"] == []
    assert ctx.replies == ["Le module Zevent n'est pas activé sur ce serveur."]


def test_the_tracker_no_longer_reloads_on_save():
    assert not getattr(Zevent, "RELOAD_ON_SAVE", False)
