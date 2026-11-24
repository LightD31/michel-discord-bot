"""A guild's "Santé" card: channels, feeds and tasks (F08)."""

import ast
import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from interactions import IntervalTrigger, OrTrigger, Permissions, Task, TimeTrigger
from source_scan import python_files, rel

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
            "rssChannelId": "10",
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


def test_a_task_is_shown_by_what_it_does_and_when():
    bot = SimpleNamespace(ext={"Rss": Other()})
    tasks = guild_health.guild_tasks(bot, {"moduleRss"})
    (entry,) = guild_health.task_report(tasks, HealthSnapshot(tasks={}))
    assert entry["name"] == "Other.poll" and entry["schedule"] == "toutes les minutes"
    label = guild_health.TASK_LABELS["LeaderboardMixin.leaderboardpermanent"]
    (known,) = guild_health.task_report(
        ["LeaderboardMixin.leaderboardpermanent"], HealthSnapshot(tasks={})
    )
    assert known["label"] == label == "Classement XP"


@pytest.mark.parametrize(
    ("trigger", "words"),
    [
        (IntervalTrigger(seconds=30), "toutes les 30 s"),
        (IntervalTrigger(minutes=5), "toutes les 5 min"),
        (IntervalTrigger(hours=1), "toutes les heures"),
        (IntervalTrigger(hours=6), "toutes les 6 h"),
        (TimeTrigger(hour=3, minute=5, utc=False), "chaque jour à 03:05"),
        (TimeTrigger(hour=9, utc=True), "chaque jour à 09:00 UTC"),
        (
            OrTrigger(TimeTrigger(hour=20, utc=False), TimeTrigger(hour=8, utc=False)),
            "chaque jour à 08:00, 20:00",
        ),
        (OrTrigger(*[TimeTrigger(hour=h, utc=False) for h in range(24)]), "toutes les heures"),
        (OrTrigger(*[TimeTrigger(hour=h, utc=False) for h in range(6)]), "6 fois par jour"),
        (OrTrigger(IntervalTrigger(minutes=1), TimeTrigger(hour=1)), ""),
    ],
)
def test_task_schedules_read_like_a_sentence(trigger, words):
    assert guild_health.task_schedule(SimpleNamespace(trigger=trigger)) == words


def _extension_tasks() -> dict[str, str]:
    """``Class.method`` of every ``@Task.create`` method in the extensions, with its file."""
    found = {}
    for path in python_files("extensions"):
        for cls in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(cls, ast.ClassDef):
                continue
            for fn in cls.body:
                if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef) and any(
                    isinstance(d, ast.Call)
                    and isinstance(d.func, ast.Attribute)
                    and d.func.attr == "create"
                    and getattr(d.func.value, "id", None) == "Task"
                    for d in fn.decorator_list
                ):
                    found[f"{cls.name}.{fn.name}"] = rel(path)
    return found


def test_every_task_has_a_label_for_the_health_card():
    tasks = _extension_tasks()
    assert len(tasks) > 30  # the scan sees the extensions
    assert [
        f"{name} ({where})" for name, where in tasks.items() if name not in guild_health.TASK_LABELS
    ] == [], "give the new Task its French label in src/webui/guild_health.py TASK_LABELS"
    assert sorted(set(guild_health.TASK_LABELS) - set(tasks)) == [], (
        "drop the label of a Task that is gone"
    )


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


def test_a_module_s_own_needs_are_checked_where_it_posts(monkeypatch):
    """P15: suggestions open threads, the XP leaderboard is pinned when asked to."""
    channel = Channel("idées", BASE | Permissions.ATTACH_FILES | Permissions.READ_MESSAGE_HISTORY)

    async def fetch(bot, guild_id, channel_id, **kwargs):
        return channel

    monkeypatch.setattr(guild_health, "fetch_guild_channel", fetch)
    bot = SimpleNamespace(get_guild=lambda gid: SimpleNamespace(me=object()))

    def missing(wanted, server):
        (entry,) = asyncio.run(guild_health.channel_report(bot, "1", wanted, server))
        return entry["missing"]

    suggestions = {"moduleSuggestions": {"enabled": True}}  # threads are on by default
    assert missing({"10": ["moduleSuggestions"]}, suggestions) == [
        "Créer des fils publics",
        "Envoyer des messages dans les fils",
    ]
    suggestions["moduleSuggestions"]["discussionThreads"] = False
    assert missing({"10": ["moduleSuggestions"]}, suggestions) == []
    xp = {"moduleXp": {"enabled": True, "xpPinMessage": True}}
    assert missing({"10": ["moduleXp"]}, xp) == ["Gérer les messages"]
    assert missing({"10": ["moduleXp"]}, {"moduleXp": {"enabled": True}}) == []


def test_each_module_says_when_its_own_tasks_last_ran():
    """P15: a module card's "a tourné il y a …", from the tasks of the extension it names first."""
    from interactions import IntervalTrigger, Task

    from src.core.health import HealthSnapshot, TaskRecord
    from src.discord_ext.task_health import task_name

    class Gazette:
        MODULE_KEYS = ("moduleGazette", "moduleXp")

        @Task.create(IntervalTrigger(minutes=15))
        async def publish_due(self):
            pass

    class Xp:
        MODULE_KEYS = ("moduleXp",)

        @Task.create(IntervalTrigger(minutes=1))
        async def tick(self):
            pass

        @Task.create(IntervalTrigger(hours=1))
        async def never(self):
            pass

    bot = SimpleNamespace(ext={"g": Gazette(), "x": Xp()})
    snapshot = HealthSnapshot(
        tasks={
            task_name(Gazette.publish_due): TaskRecord(last_success=100.0),
            task_name(Xp.tick): TaskRecord(last_success=50.0),
        }
    )
    assert guild_health.module_last_runs(bot, snapshot) == {
        "moduleGazette": 100.0,
        "moduleXp": 50.0,
    }


def test_a_module_without_its_account_is_a_problem_on_the_card(monkeypatch, store_config):
    """P36: Spotify paused for want of a connected account shows on the health card."""
    store_config({"servers": {}})
    monkeypatch.setitem(
        health_routes.ACCOUNTS, "moduleSpotify", ("Spotify", lambda guild_id: guild_id == "2")
    )

    class Ctx:
        bot = None

        async def require_guild_admin(self, request, guild_id):  # noqa: ARG002
            return SimpleNamespace(username="a", user_id="1")

        def get_full_config(self):
            on = {"moduleSpotify": {"enabled": True}}
            return {"servers": {"1": on, "2": on, "3": {}}}

    router = health_routes.create_router(Ctx())
    (route,) = [r for r in router.routes if r.path.endswith("/health")]

    def accounts(guild):
        return json.loads(asyncio.run(route.endpoint(None, guild)).body)["accounts"]

    assert accounts("1") == [{"service": "Spotify", "module": "moduleSpotify", "connected": False}]
    assert accounts("2")[0]["connected"] is True
    assert accounts("3") == []  # the module is off there
