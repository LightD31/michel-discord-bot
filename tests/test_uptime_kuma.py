"""``src.integrations.uptime_kuma``: the /metrics parser, settings, and the two HTTP calls.

``KUMA_METRICS`` is trimmed from a real Uptime Kuma 2.5.6 ``/metrics`` payload:
one group, one push monitor (whose name needs escaping), one HTTP monitor,
plus families the parser must skip (sliding-window averages, a histogram, the
app version).
"""

import pytest
from aiohttp import ClientError

import src.integrations.uptime_kuma as kuma
from src.integrations.uptime_kuma import (
    MonitorState,
    MonitorStatus,
    UptimeKumaError,
    UptimeKumaSettings,
    fetch_monitor_states,
    normalize_base_url,
    parse_metrics,
    push_heartbeat,
)

KUMA_METRICS = """\
# HELP monitor_response_time_seconds Average response time in seconds calculated over sliding window specified by the 'window' label
# TYPE monitor_response_time_seconds gauge
monitor_response_time_seconds{monitor_id="3",monitor_name="Kuma lui-même",monitor_type="http",monitor_url="http://127.0.0.1:3001/api/entry-page",monitor_hostname="null",monitor_port="null",window="1d"} 0.004
# HELP monitor_response_time Monitor Response Time (ms)
# TYPE monitor_response_time gauge
monitor_response_time{monitor_id="1",monitor_name="Maison",monitor_type="group",monitor_url="null",monitor_hostname="null",monitor_port="null"} -1
monitor_response_time{monitor_id="3",monitor_name="Kuma lui-même",monitor_type="http",monitor_url="http://127.0.0.1:3001/api/entry-page",monitor_hostname="null",monitor_port="null"} 4
monitor_response_time{monitor_id="2",monitor_name="Bot \\"test\\"",monitor_type="push",monitor_url="null",monitor_hostname="null",monitor_port="null"} 12.5
# HELP monitor_status Monitor Status (1 = UP, 0= DOWN, 2= PENDING, 3= MAINTENANCE)
# TYPE monitor_status gauge
monitor_status{monitor_id="1",monitor_name="Maison",monitor_type="group",monitor_url="null",monitor_hostname="null",monitor_port="null"} 2
monitor_status{monitor_id="3",monitor_name="Kuma lui-même",monitor_type="http",monitor_url="http://127.0.0.1:3001/api/entry-page",monitor_hostname="null",monitor_port="null"} 0
monitor_status{monitor_id="2",monitor_name="Bot \\"test\\"",monitor_type="push",monitor_url="null",monitor_hostname="null",monitor_port="null"} 1
# HELP app_version The service version by package.json
# TYPE app_version gauge
app_version{version="2.5.6",major="2",minor="5",patch="6"} 1
# HELP http_request_duration_seconds Duration of HTTP requests in seconds
# TYPE http_request_duration_seconds histogram
http_request_duration_seconds_bucket{le="0.5",status_code="200",method="GET",path="/metrics"} 1
http_request_duration_seconds_bucket{le="+Inf",status_code="200",method="GET",path="/metrics"} 1
http_request_duration_seconds_sum{status_code="200",method="GET",path="/metrics"} 0.02
http_request_duration_seconds_count{status_code="200",method="GET",path="/metrics"} 1
"""

SETTINGS = UptimeKumaSettings.from_section(
    {
        "uptimeKumaUrl": "status.example.com",
        "uptimeKumaApiKey": "uk1_secretkey",  # pragma: allowlist secret
        "uptimeKumaToken": "pushTok/en",
    }
)


# --- Parsing ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, MonitorStatus.DOWN),
        (3.0, MonitorStatus.MAINTENANCE),
        ("1", MonitorStatus.UP),
        ("pending", MonitorStatus.PENDING),
        (2.5, None),
        (7, None),
        (None, None),
        (True, None),
        ("nope", None),
    ],
)
def test_status_parse(value, expected):
    assert MonitorStatus.parse(value) is expected


def test_parse_real_payload():
    assert parse_metrics(KUMA_METRICS) == {
        1: MonitorState(1, "Maison", None, MonitorStatus.PENDING, None),
        2: MonitorState(2, 'Bot "test"', None, MonitorStatus.UP, 12.5),
        3: MonitorState(
            3,
            "Kuma lui-même",
            "http://127.0.0.1:3001/api/entry-page",
            MonitorStatus.DOWN,
            4.0,
        ),
    }


def test_parse_skips_unusable_series():
    text = (
        "# TYPE monitor_status gauge\n"
        'monitor_status{monitor_id="x",monitor_name="no id"} 1\n'
        'monitor_status{monitor_id="5",monitor_name="odd status"} 9\n'
        'monitor_status{monitor_id="6",monitor_name=""} 1\n'
    )
    assert parse_metrics(text) == {6: MonitorState(6, "ID 6", None, MonitorStatus.UP)}


def test_parse_without_monitors():
    assert parse_metrics("") == {}


def test_parse_garbage_raises():
    with pytest.raises(UptimeKumaError):
        parse_metrics("<!DOCTYPE html>\n<html>login</html>\n")


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


def test_settings_ignore_legacy_credentials():
    settings = UptimeKumaSettings.from_section(
        {"uptimeKumaUrl": "x", "uptimeKumaUsername": "admin", "uptimeKumaPassword": "p"}
    )
    assert not settings.metrics_configured
    assert not settings.push_configured
    assert SETTINGS.metrics_configured and SETTINGS.push_configured


# --- HTTP calls -------------------------------------------------------------


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

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
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


async def test_fetch_sends_the_key_as_basic_auth_password(session):
    fake = session(_Response(200, KUMA_METRICS))
    states = await fetch_monitor_states(SETTINGS)
    assert set(states) == {1, 2, 3}
    url, kwargs = fake.calls[0]
    assert url == "https://status.example.com/metrics"
    # base64(":uk1_secretkey")
    assert kwargs["headers"] == {"Authorization": "Basic OnVrMV9zZWNyZXRrZXk="}
    assert kwargs["allow_redirects"] is False


@pytest.mark.parametrize(
    ("status", "hint"),
    [(302, "redirected"), (401, "API key"), (500, "HTTP 500")],
)
async def test_fetch_errors_explain_and_hide_the_key(session, status, hint):
    session(_Response(status, "nope"))
    with pytest.raises(UptimeKumaError, match=hint) as excinfo:
        await fetch_monitor_states(SETTINGS)
    assert "secretkey" not in str(excinfo.value)


async def test_fetch_network_error(session):
    session(ClientError("boom"))
    with pytest.raises(UptimeKumaError, match="unreachable: ClientError"):
        await fetch_monitor_states(SETTINGS)


async def test_fetch_requires_a_key():
    with pytest.raises(UptimeKumaError, match="not configured"):
        await fetch_monitor_states(UptimeKumaSettings.from_section({"uptimeKumaUrl": "x"}))


async def test_push_sends_status_and_ping(session):
    fake = session(_Response(200, '{"ok":true}'))
    await push_heartbeat(SETTINGS, 42.04)
    url, kwargs = fake.calls[0]
    assert url == "https://status.example.com/api/push/pushTok%2Fen"
    assert kwargs["params"] == {"status": "up", "msg": "OK", "ping": "42.0"}


async def test_push_skips_unusable_ping(session):
    fake = session(_Response(200))
    await push_heartbeat(SETTINGS, float("inf"))
    assert "ping" not in fake.calls[0][1]["params"]


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
