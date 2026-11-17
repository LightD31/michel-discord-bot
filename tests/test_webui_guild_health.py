"""A guild's "Santé" card: channels, feeds and tasks (F08)."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from interactions import IntervalTrigger, Permissions, Task

import extensions.rss._common  # noqa: F401 — registers moduleRss
import extensions.xp._common  # noqa: F401 — registers moduleXp
import src.webui.guild_health as guild_health
import src.webui.routes.health as health_routes
from features.rss import RssFeedState
from src.core.health import HealthSnapshot, TaskRecord
from src.webui import schemas

BASE = Permissions.VIEW_CHANNEL | Permissions.SEND_MESSAGES | Permissions.EMBED_LINKS


def test_missing_permissions_are_named_and_an_admin_lacks_none():
    missing = guild_health.missing_permissions(BASE, thread=False)
    assert missing == ["Joindre des fichiers", "Voir les anciens messages"]
    assert guild_health.missing_permissions(Permissions.ADMINISTRATOR, thread=False) == []
    in_thread = guild_health.missing_permissions(BASE, thread=True)
    assert "Envoyer des messages dans les fils" in in_thread


def test_channels_of_enabled_modules_only():
    server = {
        "moduleXp": {"enabled": True, "xpChannelId": "10"},
        "moduleRss": {
            "enabled": True,
            "ChannelId": "10",
            "rssFeeds": {"a": {"url": "https://x", "channel_id": "11"}},
        },
        "moduleWelcome": {"enabled": False, "welcomeChannelId": "12"},
    }
    models = {k: schemas.module_model(k) for k in ("moduleXp", "moduleRss")}
    found = guild_health.configured_channels(server, models, schemas.channel_ids)
    assert found == {"10": ["moduleXp", "moduleRss"], "11": ["moduleRss"]}


class Channel:
    def __init__(self, name, permissions):
        self.name, self._permissions = name, permissions

    def permissions_for(self, member):
        return self._permissions


def test_the_channel_report_says_what_is_missing_and_what_is_gone(monkeypatch):
    channels = {"10": Channel("général", BASE | Permissions.ATTACH_FILES), "11": None}

    async def fetch(bot, guild_id, channel_id, **kwargs):
        return channels[channel_id]

    monkeypatch.setattr(guild_health, "fetch_guild_channel", fetch)
    bot = SimpleNamespace(get_guild=lambda gid: SimpleNamespace(me=object()))
    report = asyncio.run(
        guild_health.channel_report(bot, "1", {"10": ["moduleXp"], "11": ["moduleRss"]})
    )
    assert report == [
        {
            "channelId": "10",
            "modules": ["moduleXp"],
            "found": True,
            "name": "général",
            "missing": ["Voir les anciens messages"],
        },
        {"channelId": "11", "modules": ["moduleRss"], "found": False, "name": None, "missing": []},
    ]


def test_failing_feeds_come_first_with_their_next_try():
    now = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
    states = {
        "ok": RssFeedState(_id="ok", last_poll_at=now),
        "ko": RssFeedState(_id="ko", last_poll_at=now, last_error="HTTP 503", failures=2),
    }
    settings = {
        "rssPollMinutes": 10,
        "rssFeeds": {"ok": {"url": "u", "label": "Actus"}, "ko": {"url": "u"}, "new": {"url": "u"}},
    }
    report = guild_health.rss_report(settings, states)
    assert [f["feedId"] for f in report] == ["ko", "ok", "new"]
    assert report[0]["lastError"] == "HTTP 503"
    assert report[0]["nextPollAt"] == (now + timedelta(minutes=40)).isoformat()
    assert report[1]["label"] == "Actus" and report[2]["nextPollAt"] is None


class Ext:
    MODULE_KEYS = ("moduleXp",)

    @Task.create(IntervalTrigger(minutes=1))
    async def tick(self):
        pass


class Other:
    MODULE_KEYS = ("moduleRss",)

    @Task.create(IntervalTrigger(minutes=1))
    async def poll(self):
        pass


def test_only_the_tasks_serving_the_guild_are_listed():
    bot = SimpleNamespace(ext={"Xp": Ext(), "Rss": Other()})
    names = guild_health.guild_task_names(bot, {"moduleXp"})
    assert names == ["Ext.tick"]
    snapshot = HealthSnapshot(
        tasks={"Ext.tick": TaskRecord(successes=3, failures=1, last_failure=5.0)}
    )
    (entry,) = guild_health.task_report(names, snapshot)
    assert entry["successes"] == 3 and entry["failures"] == 1 and entry["lastFailure"] == 5.0


def test_the_route_without_a_bot_still_reports_the_feeds(monkeypatch, store_config):
    store_config({"servers": {}})

    class Repo:
        def __init__(self, guild_id):
            pass

        async def states(self):
            return {"a": RssFeedState(_id="a", failures=1, last_error="timeout")}

    monkeypatch.setattr(health_routes, "RssRepository", Repo)

    class Ctx:
        bot = None

        async def require_guild_admin(self, request, guild_id):  # noqa: ARG002
            return SimpleNamespace(username="a", user_id="1")

        def get_full_config(self):
            return {
                "servers": {"1": {"moduleRss": {"enabled": True, "rssFeeds": {"a": {"url": "u"}}}}}
            }

    router = health_routes.create_router(Ctx())
    (route,) = [r for r in router.routes if r.path.endswith("/health")]
    body = json.loads(asyncio.run(route.endpoint(None, "1")).body)
    assert body["channels"] is None and body["tasks"] == []
    assert body["feeds"][0]["lastError"] == "timeout"
