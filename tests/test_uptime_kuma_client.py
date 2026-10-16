"""``src.integrations.uptime_kuma``: payload parsing, settings, the push call and
the Socket.IO event handlers (driven directly, no server).

Payload shapes mirror Uptime Kuma 2.5: live ``heartbeat`` events carry
``monitorID``; ``heartbeatList`` rows are raw SQL rows (``monitor_id``, oldest
first) sent as ``(monitorID, rows, overwrite)``.
"""

from datetime import UTC, datetime

import pytest
import socketio
from aiohttp import ClientError

import src.integrations.uptime_kuma as kuma
from src.integrations.uptime_kuma import (
    Heartbeat,
    Monitor,
    MonitorStatus,
    UptimeKumaClient,
    UptimeKumaError,
    UptimeKumaSettings,
    latest_heartbeat,
    normalize_base_url,
    push_heartbeat,
)

SETTINGS = UptimeKumaSettings.from_section(
    {
        "uptimeKumaUrl": "status.example.com",
        "uptimeKumaUsername": "bot",
        "uptimeKumaPassword": "hunter2",  # pragma: allowlist secret
        "uptimeKumaToken": "pushTok/en",
    }
)


# --- Parsing ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, MonitorStatus.DOWN),
        (3, MonitorStatus.MAINTENANCE),
        ("1", MonitorStatus.UP),
        ("pending", MonitorStatus.PENDING),
        ("DOWN", MonitorStatus.DOWN),
        (7, None),
        (None, None),
        (True, None),
        ("nope", None),
    ],
)
def test_status_parse(value, expected):
    assert MonitorStatus.parse(value) is expected


def test_monitor_drops_kuma_url_placeholder():
    ping = Monitor.from_payload({"id": 4, "name": "Pi", "url": "https://", "pathName": ""})
    web = Monitor.from_payload(
        {
            "id": "5",
            "name": "Immich",
            "url": "https://photos.example",
            "pathName": "Proxmox / Immich",
        }
    )
    assert ping == Monitor(id=4, name="Pi", path_name="", url=None)
    assert ping.display_name == "Pi"
    assert web.url == "https://photos.example"
    assert web.display_name == "Proxmox / Immich"
    assert Monitor.from_payload({"name": "no id"}) is None


def test_live_heartbeat():
    beat = Heartbeat.from_payload(
        {
            "monitorID": 12,
            "status": 0,
            "time": "2026-10-16 08:30:00.123",
            "msg": "timeout",
            "ping": None,
            "important": True,
        }
    )
    assert beat == Heartbeat(
        monitor_id=12,
        status=MonitorStatus.DOWN,
        time=datetime(2026, 10, 16, 8, 30, 0, 123000, tzinfo=UTC),
        msg="timeout",
        ping=None,
    )


def test_heartbeat_list_takes_newest_valid_row():
    rows = [
        {"monitor_id": 3, "status": 1, "time": "2026-10-16 08:00:00", "ping": 20},
        {"monitor_id": 3, "status": 0, "time": "2026-10-16 08:01:00", "ping": 21.5},
        {"monitor_id": 3, "status": 9},
    ]
    beat = latest_heartbeat("3", rows)
    assert beat.monitor_id == 3
    assert beat.status is MonitorStatus.DOWN
    assert beat.ping == 21.5
    assert latest_heartbeat("3", []) is None
    assert latest_heartbeat("x", rows) is None


# --- Settings ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("status.example.com", "https://status.example.com"),
        ("status.example.com/", "https://status.example.com"),
        ("http://uptime-kuma:3001/", "http://uptime-kuma:3001"),
        ("  ", ""),
    ],
)
def test_normalize_base_url(raw, expected):
    assert normalize_base_url(raw) == expected


def test_settings_configured_flags():
    assert SETTINGS.socket_configured and SETTINGS.push_configured
    no_password = UptimeKumaSettings.from_section({"uptimeKumaUrl": "x", "uptimeKumaUsername": "u"})
    assert not no_password.socket_configured
    assert not no_password.push_configured
    assert not UptimeKumaSettings.from_section({}).socket_configured


# --- Push -------------------------------------------------------------------


class _Response:
    def __init__(self, status, body=""):
        self.status = status
        self._body = body

    async def text(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def get(self, url, params=None):
        self.calls.append((url, params))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.fixture
def session(monkeypatch):
    holder = {}

    async def fake_session():
        return holder["session"]

    monkeypatch.setattr(kuma.http_client, "session", fake_session)

    def use(outcome):
        holder["session"] = _Session(outcome)
        return holder["session"]

    return use


async def test_push_sends_status_and_ping(session):
    fake = session(_Response(200, '{"ok":true}'))
    await push_heartbeat(SETTINGS, 42.04)
    url, params = fake.calls[0]
    assert url == "https://status.example.com/api/push/pushTok%2Fen"
    assert params == {"status": "up", "msg": "OK", "ping": "42.0"}


async def test_push_skips_unusable_ping(session):
    fake = session(_Response(200))
    await push_heartbeat(SETTINGS, float("inf"))
    assert "ping" not in fake.calls[0][1]


async def test_push_refused_does_not_leak_token(session):
    session(_Response(404, '{"ok":false,"msg":"Monitor not found or not active."}'))
    with pytest.raises(UptimeKumaError) as excinfo:
        await push_heartbeat(SETTINGS, None)
    assert "404" in str(excinfo.value)
    assert "pushTok" not in str(excinfo.value)


async def test_push_network_error(session):
    session(ClientError("boom"))
    with pytest.raises(UptimeKumaError, match="ClientError"):
        await push_heartbeat(SETTINGS, None)


async def test_push_requires_token():
    with pytest.raises(UptimeKumaError):
        await push_heartbeat(UptimeKumaSettings.from_section({"uptimeKumaUrl": "x"}), None)


# --- Socket.IO handlers -----------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(UptimeKumaSettings, "load", classmethod(lambda cls: SETTINGS))
    beats: list[Heartbeat] = []

    async def on_heartbeat(beat):
        beats.append(beat)

    c = UptimeKumaClient(on_heartbeat)
    c.beats = beats
    return c


async def test_monitor_list_updates_and_deletes(client):
    await client._on_monitor_list(
        {"1": {"id": 1, "name": "VPS"}, "2": {"id": 2, "name": "Pi"}, "x": "junk"}
    )
    assert set(client.monitors) == {1, 2}
    await client._on_update_monitors({"2": {"id": 2, "name": "Raspberry"}})
    assert client.monitors[2].name == "Raspberry"
    await client._on_delete_monitor(1)
    assert set(client.monitors) == {2}


async def test_heartbeats_are_dispatched_and_old_ones_dropped(client):
    await client._on_heartbeat_list(
        5, [{"monitor_id": 5, "status": 1, "time": "2026-10-16 08:00:00"}], False
    )
    await client._on_heartbeat_event({"monitorID": 5, "status": 0, "time": "2026-10-16 08:05:00"})
    # A stale history row arriving after the live beat must not roll the state back.
    await client._on_heartbeat_list(
        5, [{"monitor_id": 5, "status": 1, "time": "2026-10-16 08:00:00"}], False
    )
    assert [b.status for b in client.beats] == [MonitorStatus.UP, MonitorStatus.DOWN]
    assert client.latest_status(5) is MonitorStatus.DOWN
    assert client.latest_status(6) is None


async def test_failing_handler_does_not_escape(monkeypatch):
    async def broken(beat):
        raise RuntimeError("bug")

    c = UptimeKumaClient(broken)
    await c._on_heartbeat_event({"monitorID": 1, "status": 1})
    assert c.latest_status(1) is MonitorStatus.UP


@pytest.mark.parametrize(
    ("response", "ok"),
    [
        ({"ok": True, "token": "jwt"}, True),
        ({"tokenRequired": True}, False),
        ({"ok": False, "msg": "authIncorrectCreds"}, False),
        (None, False),
    ],
)
async def test_login_outcomes(client, monkeypatch, response, ok):
    sent = {}

    async def fake_call(event, data=None, timeout=None):
        sent["event"], sent["data"] = event, data
        return response

    monkeypatch.setattr(client._sio, "call", fake_call)
    monkeypatch.setattr(client._sio, "connected", True)
    assert await client.login() is ok
    assert sent == {
        "event": "login",
        "data": {"username": "bot", "password": SETTINGS.password, "token": ""},
    }
    assert client.ready is ok
    assert client.needs_login is not ok


async def test_login_timeout(client, monkeypatch):
    async def fake_call(event, data=None, timeout=None):
        raise socketio.exceptions.TimeoutError()

    monkeypatch.setattr(client._sio, "call", fake_call)
    assert await client.login() is False


async def test_disconnect_clears_login(client, monkeypatch):
    async def fake_call(event, data=None, timeout=None):
        return {"ok": True}

    monkeypatch.setattr(client._sio, "call", fake_call)
    monkeypatch.setattr(client._sio, "connected", True)
    await client.login()
    await client._on_disconnect("transport close")
    assert not client.ready


async def test_close_without_start(client):
    await client.close()
    client.start()  # a closed client stays closed
    assert client._task is None
