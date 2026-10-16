"""``features.uptime``: transition rules, stored-alert parsing, embed texts and the repository."""

import copy
from datetime import UTC, datetime

import pytest

import features.uptime.repository as repository
from features.uptime import (
    AlertEvent,
    AlertMode,
    AlertTone,
    MonitorAlert,
    Transition,
    UptimeRepository,
    render_alert,
    transition,
)
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


# --- Stored alerts ----------------------------------------------------------


def test_alert_round_trip():
    alert = MonitorAlert(monitor_id=21, channel_id=123, mode=AlertMode.SIMPLE, last_status=DOWN)
    assert alert.to_mongo() == {"channel_id": 123, "mode": "simple", "last_status": 0}
    assert MonitorAlert.from_mongo("21", alert.to_mongo()) == alert


@pytest.mark.parametrize(
    ("doc", "expected"),
    [
        # Written by the previous implementation: no mode, int status.
        ({"channel_id": 5, "last_status": 1}, MonitorAlert(7, 5, AlertMode.DETAILED, UP)),
        ({"channel_id": "5", "last_status": None, "mode": "weird"}, MonitorAlert(7, 5)),
        ({"channel_id": 5, "last_status": "MAINTENANCE"}, MonitorAlert(7, 5, last_status=MAINT)),
        ({"last_status": 1}, None),
        ({"channel_id": "abc"}, None),
        ("not a dict", None),
    ],
)
def test_alert_from_mongo(doc, expected):
    assert MonitorAlert.from_mongo("7", doc) == expected


# --- Rendering --------------------------------------------------------------


def test_simple_alert():
    message = render_alert(
        AlertEvent.DOWN,
        AlertMode.SIMPLE,
        monitor_id=3,
        monitor_name="VPS",
        previous=UP,
        current=DOWN,
        message="timeout",
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
        url="https://photos.example",
        message="x" * 2000,
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
    assert fields["URL"] == "https://photos.example"
    assert len(fields["Message"]) == 1024
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
    """Just enough of motor for the repository: one document, ``$set``/``$unset``/``$exists``."""

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
    col = _FakeCollection(
        {
            "_id": "config",
            "21": {"channel_id": 100, "last_status": 1, "mode": "simple"},
            "22": {"channel_id": 101, "last_status": None},
            "bad": {"oops": True},
        }
    )
    monkeypatch.setattr(
        repository.mongo_manager, "get_guild_collection", lambda guild_id, name: col
    )
    return col


async def test_load_skips_malformed(collection):
    alerts = await UptimeRepository("1").load()
    assert alerts == {
        21: MonitorAlert(21, 100, AlertMode.SIMPLE, UP),
        22: MonitorAlert(22, 101),
    }


async def test_remove_is_persisted(collection):
    repo = UptimeRepository("1")
    await repo.remove(21)
    assert set(await repo.load()) == {22}


async def test_status_update_never_resurrects_removed_alert(collection):
    repo = UptimeRepository("1")
    await repo.remove(21)
    await repo.set_last_status(21, DOWN)
    await repo.set_last_status(22, DOWN)
    alerts = await repo.load()
    assert set(alerts) == {22}
    assert alerts[22].last_status is DOWN


async def test_save_into_empty_guild(monkeypatch):
    col = _FakeCollection()
    monkeypatch.setattr(
        repository.mongo_manager, "get_guild_collection", lambda guild_id, name: col
    )
    repo = UptimeRepository("1")
    await repo.save(MonitorAlert(5, 200, AlertMode.DETAILED, None))
    assert await repo.load() == {5: MonitorAlert(5, 200)}
