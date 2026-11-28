"""``features.uptime``: transition rules, stored-alert parsing, embed texts and the repository."""

import asyncio
import copy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from features.uptime import (
    AlertEvent,
    AlertMode,
    AlertTone,
    MonitorAlert,
    StatusChange,
    Transition,
    UptimeRepository,
    alerts_from_settings,
    apply_statuses,
    render_alert,
    transition,
)
from src.core import db
from src.integrations.uptime_kuma import MonitorStatus

DOWN, UP, PENDING, MAINT = (
    MonitorStatus.DOWN,
    MonitorStatus.UP,
    MonitorStatus.PENDING,
    MonitorStatus.MAINTENANCE,
)

# --- Transitions ------------------------------------------------------------


@pytest.mark.parametrize(
    ("previous", "current", "expected"),
    [
        # First settled beat: baseline only.
        (None, DOWN, Transition(DOWN)),
        (None, UP, Transition(UP)),
        (None, PENDING, Transition(None)),
        # No change.
        (UP, UP, Transition(UP)),
        (DOWN, DOWN, Transition(DOWN)),
        # Retries never post and never overwrite the settled status.
        (UP, PENDING, Transition(UP)),
        (DOWN, PENDING, Transition(DOWN)),
        # Changes worth posting.
        (UP, DOWN, Transition(DOWN, AlertEvent.DOWN)),
        (MAINT, DOWN, Transition(DOWN, AlertEvent.DOWN)),
        (UP, MAINT, Transition(MAINT, AlertEvent.MAINTENANCE)),
        (DOWN, MAINT, Transition(MAINT, AlertEvent.MAINTENANCE)),
        (DOWN, UP, Transition(UP, AlertEvent.RECOVERED)),
        (MAINT, UP, Transition(UP, AlertEvent.MAINTENANCE_ENDED)),
        # Older data stored PENDING as the last status.
        (PENDING, UP, Transition(UP)),
        (PENDING, DOWN, Transition(DOWN, AlertEvent.DOWN)),
    ],
)
def test_transition(previous, current, expected):
    assert transition(previous, current) == expected


def test_retry_then_down_posts_once():
    status = UP
    events = []
    for beat in (PENDING, PENDING, DOWN, DOWN, UP):
        change = transition(status, beat)
        status = change.status
        events.append(change.event)
    assert events == [None, None, AlertEvent.DOWN, None, AlertEvent.RECOVERED]


def test_apply_statuses_across_guilds():
    shared_a = MonitorAlert(1, 100, last_status=UP)
    shared_b = MonitorAlert(1, 200, last_status=DOWN)
    fresh = MonitorAlert(2, 100)
    paused = MonitorAlert(3, 100, last_status=UP)
    alerts = {"a": {1: shared_a, 2: fresh, 3: paused}, "b": {1: shared_b}}

    changes = apply_statuses(alerts, {1: DOWN, 2: PENDING, 4: UP})

    # Guild b already knew monitor 1 was down; monitor 2 is still retrying;
    # monitor 3 is absent from /metrics (paused) and keeps its status.
    assert changes == [StatusChange("a", shared_a, UP, AlertEvent.DOWN)]
    assert shared_a.last_status is DOWN
    assert fresh.last_status is None
    assert paused.last_status is UP

    changes = apply_statuses(alerts, {1: UP, 2: UP})
    assert [(c.guild_id, c.alert.monitor_id, c.previous, c.event) for c in changes] == [
        ("a", 1, DOWN, AlertEvent.RECOVERED),
        ("a", 2, None, None),  # baseline: persisted, not posted
        ("b", 1, DOWN, AlertEvent.RECOVERED),
    ]
    assert apply_statuses(alerts, {1: UP, 2: UP}) == []


# --- Alerts as settings (P42) ---------------------------------------------


def test_alert_round_trip():
    alert = MonitorAlert(monitor_id=21, channel_id=123, mode=AlertMode.SIMPLE)
    assert alert.to_setting() == {"monitorId": "21", "channelId": "123", "mode": "simple"}
    assert MonitorAlert.from_setting(alert.to_setting()) == alert


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ({"monitorId": "7", "channelId": "5"}, MonitorAlert(7, 5, AlertMode.DETAILED)),
        ({"monitorId": 7, "channelId": "5", "mode": "weird"}, MonitorAlert(7, 5)),
        ({"channelId": "5"}, None),
        ({"monitorId": "7", "channelId": "abc"}, None),
        ("not a dict", None),
    ],
)
def test_alert_from_setting(entry, expected):
    assert MonitorAlert.from_setting(entry) == expected


def test_the_alerts_are_the_settings_with_the_last_status_seen():
    settings = {
        "alerts": [
            {"monitorId": "21", "channelId": "100", "mode": "simple"},
            {"monitorId": "22", "channelId": "101"},
            {"monitorId": "oops"},
        ]
    }
    assert alerts_from_settings(settings, {21: UP, 99: DOWN}) == {
        21: MonitorAlert(21, 100, AlertMode.SIMPLE, UP),
        22: MonitorAlert(22, 101),
    }
    assert alerts_from_settings({}, {21: UP}) == {}


# --- Rendering --------------------------------------------------------------


def test_simple_alert():
    message = render_alert(
        AlertEvent.DOWN,
        AlertMode.SIMPLE,
        monitor_id=3,
        monitor_name="VPS",
        previous=UP,
        current=DOWN,
    )
    assert message.title == "❌ Hors ligne"
    assert message.description == "**VPS** est hors ligne"
    assert message.tone is AlertTone.DOWN
    assert [(f.name, f.value) for f in message.fields] == [("Statut", "Hors ligne")]
    assert message.timestamp is None


def test_detailed_alert():
    at = datetime(2026, 10, 16, 8, 0, tzinfo=UTC)
    message = render_alert(
        AlertEvent.MAINTENANCE_ENDED,
        AlertMode.DETAILED,
        monitor_id=3,
        monitor_name="Proxmox / Immich",
        previous=MAINT,
        current=UP,
        url="https://photos.example/" + "x" * 2000,
        ping=12.0,
        at=at,
    )
    assert message.title == "✅ Fin de maintenance"
    assert "**Proxmox / Immich**" in message.description
    assert message.tone is AlertTone.UP
    fields = {f.name: f.value for f in message.fields}
    assert fields["ID du capteur"] == "3"
    assert fields["État actuel"] == "En ligne"
    assert fields["État précédent"] == "Maintenance"
    assert fields["URL"].startswith("https://photos.example/")
    assert len(fields["URL"]) == 1024
    assert fields["Ping"] == "12 ms"
    assert message.timestamp == at


def test_detailed_alert_omits_empty_fields():
    message = render_alert(
        AlertEvent.MAINTENANCE,
        AlertMode.DETAILED,
        monitor_id=3,
        monitor_name="VPS",
        previous=None,
        current=MAINT,
    )
    assert [f.name for f in message.fields] == ["ID du capteur", "État actuel"]


# --- Repository -------------------------------------------------------------


class _FakeCollection:
    """Just enough of the driver for the repository: one document, ``$set``/``$unset``/``$exists``."""

    def __init__(self, doc=None):
        self.doc = copy.deepcopy(doc)

    async def find_one(self, query):
        if self.doc is not None and self.doc["_id"] == query["_id"]:
            return copy.deepcopy(self.doc)
        return None

    async def update_one(self, query, update, upsert=False):
        if self.doc is None:
            if not upsert:
                return
            self.doc = {"_id": query["_id"]}
        for key, cond in query.items():
            if isinstance(cond, dict) and cond.get("$exists") and key not in self.doc:
                return
        for path, value in update.get("$set", {}).items():
            target = self.doc
            *parents, leaf = path.split(".")
            for part in parents:
                target = target.setdefault(part, {})
            target[leaf] = value
        for key in update.get("$unset", {}):
            self.doc.pop(key, None)


@pytest.fixture
def collection(monkeypatch):
    col = _FakeCollection({"_id": "statuses", "21": 1, "22": None, "bad": 1, "23": "UP"})
    monkeypatch.setattr(db.mongo_manager, "get_guild_collection", lambda guild_id, name: col)
    return col


async def test_the_statuses_skip_what_is_malformed(collection):
    assert await UptimeRepository("1").load_statuses() == {21: UP, 22: None, 23: None}


async def test_a_status_is_saved_and_forgotten(collection):
    repo = UptimeRepository("1")
    await repo.set_last_status(22, DOWN)
    await repo.forget(21)
    statuses = await repo.load_statuses()
    assert 21 not in statuses and statuses[22] is DOWN


async def test_a_first_status_creates_the_document(monkeypatch):
    col = _FakeCollection()
    monkeypatch.setattr(db.mongo_manager, "get_guild_collection", lambda guild_id, name: col)
    repo = UptimeRepository("1")
    await repo.set_last_status(5, UP)
    assert await repo.load_statuses() == {5: UP}


async def test_each_server_reads_its_own_kuma(monkeypatch, store_config):
    """P37: one read per instance and key; a server's alerts follow its own Kuma only."""
    import extensions.uptime.tasks as tasks_mod
    from extensions.uptime import UptimeExtension
    from src.integrations.uptime_kuma import UptimeKumaError

    def kuma(url):
        return {"enabled": True, "uptimeKumaUrl": url, "uptimeKumaApiKey": "k"}

    store_config(
        {
            "servers": {
                "1": {"moduleUptime": kuma("https://a.example")},
                "2": {"moduleUptime": kuma("https://a.example")},
                "3": {"moduleUptime": kuma("https://b.example")},
                "4": {"moduleUptime": {"enabled": True}},  # no instance of its own
            }
        }
    )
    reads: list[str] = []

    async def fetch(settings):
        reads.append(settings.base_url)
        if settings.base_url == "https://b.example":
            raise UptimeKumaError("down")
        return {7: SimpleNamespace(name="NAS", status=MonitorStatus.UP)}

    applied: list[str] = []
    monkeypatch.setattr(tasks_mod, "fetch_monitor_states", fetch)
    ext = object.__new__(UptimeExtension)
    ext._alerts_loaded, ext.monitors, ext._poll_failing = True, {}, set()
    ext.kuma_ok = {"3", "4"}

    async def apply_states(guild_id, states):
        applied.append(guild_id)

    ext.apply_states = apply_states
    await UptimeExtension.poll_kuma.callback(ext)

    assert reads == ["https://a.example", "https://b.example"]
    assert applied == ["1", "2"] and ext.kuma_ok == {"1", "2"}
    assert set(ext.monitors) == {"1", "2"}


async def test_the_commands_write_the_alerts_in_the_modules_settings(monkeypatch, store_config):
    """P42: /uptime configurer and supprimer change moduleUptime.alerts, as the dashboard does."""
    from discord_fakes import FakeChannel, FakeCtx, FakeGuild, FakeMember, bare_extension, run
    from interactions import Permissions

    import extensions.uptime.commands as commands_mod
    from extensions.uptime import UptimeExtension
    from src.core.config import config_store

    data = store_config({"servers": {"1": {"moduleUptime": {"enabled": True}}}})
    # config.json on disk is the developer's: the writes go to the store's dict.
    monkeypatch.setattr(config_store, "mutate", lambda mutator: mutator(data))
    saved: list = []

    class Repo:
        def __init__(self, guild_id):
            pass

        async def set_last_status(self, monitor_id, status):
            saved.append(("status", monitor_id, status))

        async def forget(self, monitor_id):
            saved.append(("forget", monitor_id))

    monkeypatch.setattr(commands_mod, "UptimeRepository", Repo)
    guild = FakeGuild(1)
    author = guild.add_member(FakeMember(7, permissions=Permissions.ALL))
    here = guild.add_channel(FakeChannel(10))
    ctx = FakeCtx(author, here)
    ext = bare_extension(
        UptimeExtension,
        ctx.bot,
        kuma_ok={"1"},
        monitors={"1": {5: SimpleNamespace(id=5, name="NAS", status=MonitorStatus.UP)}},
        statuses={},
        _state_lock=asyncio.Lock(),
    )

    await run(UptimeExtension.setup_maintenance_alert, ext, ctx, "5", here, "simple")
    alerts = config_store.get()["servers"]["1"]["moduleUptime"]["alerts"]
    assert alerts == [{"monitorId": "5", "channelId": "10", "mode": "simple"}]
    assert ext.alerts_of("1") == {5: MonitorAlert(5, 10, AlertMode.SIMPLE, UP)}  # its baseline
    assert ("status", 5, UP) in saved

    await run(UptimeExtension.remove_maintenance_alert, ext, ctx, "5")
    assert config_store.get()["servers"]["1"]["moduleUptime"]["alerts"] == []
    assert ("forget", 5) in saved and ext.alerts_of("1") == {}
