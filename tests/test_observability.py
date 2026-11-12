"""Observability: log context and formats, task and HTTP health, backup age, loop lag, Sentry."""

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from aiohttp import web
from interactions import ComponentContext, IntervalTrigger, ModalContext, Task
from prometheus_client import CollectorRegistry, generate_latest

import src.discord_ext.task_health as task_health
from features.metrics import HealthCollector
from src.core import backup_format, error_reporting, log_context
from src.core.errors import HttpError
from src.core.health import HealthRegistry, health, http_outcome, watch_loop_lag
from src.core.http import fetch, http_client
from src.core.logging import CustomFormatter, JsonFormatter
from src.discord_ext.errors import new_reference
from src.discord_ext.interaction_context import bind_interaction_context, describe
from src.webui.log_handler import WebUILogHandler


@pytest.fixture(autouse=True)
def fresh_health():
    health.clear()
    yield
    health.clear()


def record(message="hello", level=logging.INFO, exc_info=None) -> logging.LogRecord:
    # makeRecord goes through the record factory, like a real logging call.
    return logging.getLogger("test.obs").makeRecord(
        "test.obs", level, __file__, 1, message, (), exc_info
    )


# ── Log context ─────────────────────────────────────────────────────────


def test_scope_binds_and_restores():
    assert log_context.current() == {}
    with log_context.scope(guild_id=42, command="/bilan"):
        assert log_context.current() == {"guild_id": "42", "command": "/bilan"}
        with log_context.scope(command=None, task="t"):
            assert log_context.current() == {"guild_id": "42", "task": "t"}
        assert log_context.current()["command"] == "/bilan"
    assert log_context.current() == {}


async def test_concurrent_interactions_keep_their_own_context():
    seen = {}

    async def handle(ref):
        log_context.bind(correlation_id=ref)
        await asyncio.sleep(0.01)
        seen[ref] = log_context.correlation_id()

    await asyncio.gather(handle("aaa"), handle("bbb"))
    assert seen == {"aaa": "aaa", "bbb": "bbb"}
    assert log_context.correlation_id() is None  # gather ran them in their own tasks


def test_records_snapshot_the_context():
    with log_context.scope(correlation_id="abc123", guild_id="42"):
        rec = record()
    assert rec.log_context == {"correlation_id": "abc123", "guild_id": "42"}


def test_json_lines_carry_context_and_exception():
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        import sys

        with log_context.scope(correlation_id="abc123", command="/bilan"):
            rec = record("échec", logging.ERROR, exc_info=sys.exc_info())
    line = json.loads(JsonFormatter().format(rec))
    assert line["message"] == "échec" and line["level"] == "ERROR"
    assert line["correlation_id"] == "abc123" and line["command"] == "/bilan"
    assert "RuntimeError: boom" in line["exception"]
    assert line["time"].endswith("+00:00")


def test_text_lines_end_with_the_context_and_keep_the_traceback_below():
    with log_context.scope(correlation_id="abc123", guild_id="42"):
        rec = record("hello")
    first_line = CustomFormatter().format(rec).splitlines()[0]
    assert first_line.endswith("[ref=abc123 guild=42]")
    assert CustomFormatter().format(record("plain")).endswith("plain\x1b[0m")


def test_the_dashboard_log_stream_shows_the_reference():
    handler = WebUILogHandler(max_entries=5)
    with log_context.scope(correlation_id="abc123"):
        handler.emit(record("hello"))
    assert handler.buffer[-1].message == "hello [correlation_id=abc123]"


# ── Interaction context ─────────────────────────────────────────────────


def bare(cls, **attrs):
    ctx = cls.__new__(cls)
    for name, value in attrs.items():
        object.__setattr__(ctx, name, value)
    return ctx


def test_describe_names_commands_components_and_modals():
    assert describe(SimpleNamespace(invoke_target="tricount bilan")) == "/tricount bilan"
    assert describe(bare(ComponentContext, custom_id="poll:1")) == "component:poll:1"
    assert describe(bare(ModalContext, custom_id="edit")) == "modal:edit"


async def test_an_interaction_gets_a_reference_the_error_reply_reuses():
    async def interaction():
        await bind_interaction_context(SimpleNamespace(invoke_target="bilan", guild_id=42))
        context = log_context.current()
        return context, new_reference()

    context, reference = await asyncio.create_task(interaction())
    assert context["guild_id"] == "42" and context["command"] == "/bilan"
    assert reference == context["correlation_id"] and len(reference) == 6
    assert len(new_reference()) == 6  # outside an interaction: a fresh one


# ── Task health ─────────────────────────────────────────────────────────


@pytest.fixture
def installed(monkeypatch):
    task_health.install()
    errors = []
    monkeypatch.setattr(
        "interactions.Client.default_error_handler",
        staticmethod(lambda source, error: errors.append((source, error))),
    )
    captured = []
    monkeypatch.setattr(error_reporting, "capture", captured.append)
    return SimpleNamespace(library_errors=errors, captured=captured)


class Poller:
    seen_context: dict = {}

    @Task.create(IntervalTrigger(minutes=5))
    async def poll(self, fail=False):
        type(self).seen_context = log_context.current()
        if fail:
            raise RuntimeError("API down")


async def test_a_successful_run_is_recorded_with_its_task_context(installed):
    task = Poller.poll
    await task(Poller(), False)
    ((name, rec),) = health.snapshot().tasks.items()
    assert name == "Poller.poll"
    assert (rec.successes, rec.failures) == (1, 0) and rec.last_success is not None
    assert Poller.seen_context["task"] == "Poller.poll"
    assert len(Poller.seen_context["correlation_id"]) == 6
    assert log_context.current() == {}  # restored after the run


async def test_a_failing_run_is_recorded_reported_and_still_logged_by_the_library(installed):
    await Poller.poll(Poller(), True)
    rec = health.snapshot().tasks["Poller.poll"]
    assert (rec.successes, rec.failures) == (0, 1) and rec.last_failure is not None
    assert [type(e) for e in installed.captured] == [RuntimeError]
    assert [source for source, _ in installed.library_errors] == ["Task"]


async def test_install_is_idempotent(installed):
    call = Task.__call__
    task_health.install()
    assert Task.__call__ is call


async def test_a_cancelled_run_counts_as_nothing(installed):
    @Task.create(IntervalTrigger(minutes=5))
    async def slow():
        await asyncio.sleep(10)

    run = asyncio.create_task(slow())
    await asyncio.sleep(0.01)
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run
    assert health.snapshot().tasks == {}


async def test_a_firing_during_a_run_is_skipped_and_counted(installed):
    release = asyncio.Event()
    runs = []

    @Task.create(IntervalTrigger(seconds=30))
    async def slow():
        runs.append(1)
        await release.wait()

    first = slow._fire(datetime.now())
    await asyncio.sleep(0.01)
    assert task_health.is_running(slow)

    fire_time = datetime.now()
    second = slow._fire(fire_time)
    assert second.done()  # nothing scheduled
    assert slow.trigger.last_call_time == fire_time  # next firing computed from this one
    assert slow.iteration == 1

    release.set()
    await first
    assert runs == [1]
    assert not task_health.is_running(slow)
    record = health.snapshot().tasks[task_health.task_name(slow)]
    assert (record.successes, record.skipped) == (1, 1)

    third = slow._fire(datetime.now())  # once finished, the task runs again
    await third
    assert runs == [1, 1]


async def test_a_direct_call_in_progress_also_blocks_a_firing(installed):
    release = asyncio.Event()

    @Task.create(IntervalTrigger(seconds=30))
    async def slow():
        await release.wait()

    direct = asyncio.create_task(slow())
    await asyncio.sleep(0.01)
    assert slow._fire(datetime.now()).done()
    release.set()
    await direct
    assert health.snapshot().tasks[task_health.task_name(slow)].skipped == 1


# ── HTTP health ─────────────────────────────────────────────────────────


@pytest.fixture
async def server():
    async def ok(_):
        return web.Response(text="ok")

    async def down(_):
        return web.Response(status=503)

    app = web.Application()
    app.router.add_get("/ok", ok)
    app.router.add_get("/down", down)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    await runner.cleanup()
    await http_client.close()


async def test_every_attempt_is_counted_per_host_and_outcome(server):
    assert await fetch(f"{server}/ok") == "ok"
    with pytest.raises(HttpError):
        await fetch(f"{server}/down", retries=2, pause=0)
    with pytest.raises(HttpError):
        await fetch("http://127.0.0.1:9/nothing-listens", retries=1)
    assert health.snapshot().http_requests == {
        ("127.0.0.1", "2xx"): 1,
        ("127.0.0.1", "5xx"): 2,
        ("127.0.0.1", "error"): 1,
    }


@pytest.mark.parametrize(
    ("status", "outcome"), [(200, "2xx"), (301, "3xx"), (404, "4xx"), (503, "5xx"), (None, "error")]
)
def test_http_outcome(status, outcome):
    assert http_outcome(status) == outcome


# ── Backups, registry and exporter ──────────────────────────────────────


def test_the_newest_complete_backup_gives_the_age(tmp_path):
    assert backup_format.latest_backup_time(tmp_path / "missing") is None
    for name in ("2026-03-08_04-00-00", "2026-03-09_04-00-00"):
        (tmp_path / name).mkdir()
        backup_format.write_manifest(tmp_path / name, name, {})
    (tmp_path / "2026-03-10_04-00-00").mkdir()  # interrupted: no manifest
    # The folder name is a wall time in Paris: 04:00 there is 03:00 UTC in March.
    expected = datetime(2026, 3, 9, 3, 0, tzinfo=UTC).timestamp()
    assert backup_format.latest_backup_time(tmp_path) == expected


def test_the_registry_keeps_the_latest_backup_and_hands_out_copies():
    registry = HealthRegistry()
    registry.record_backup(200.0)
    registry.record_backup(100.0)  # an older folder read from disk
    registry.record_task("t", ok=True, duration=0.5, at=10.0)
    registry.record_task("t", ok=False, duration=1.5, at=20.0)
    snap = registry.snapshot()
    registry.record_task("t", ok=True, duration=0.1, at=30.0)
    assert snap.last_backup == 200.0
    rec = snap.tasks["t"]
    assert (rec.successes, rec.failures, rec.last_success, rec.last_failure) == (1, 1, 10.0, 20.0)
    assert rec.last_duration == 1.5


def test_the_exporter_serves_the_health_series():
    health.record_task("YoutubeExtension.check_youtube", ok=True, duration=2.0, at=1000.0)
    health.record_task("YoutubeExtension.check_youtube", ok=False, duration=1.0, at=1100.0)
    health.record_http("www.googleapis.com", 503)
    health.record_background_failure("twitch-eventsub")
    health.record_backup(1234.0)
    registry = CollectorRegistry()
    registry.register(HealthCollector(health.snapshot))
    text = generate_latest(registry).decode()
    task = 'task="YoutubeExtension.check_youtube"'
    assert f'michel_task_runs_total{{status="ok",{task}}} 1.0' in text
    assert f'michel_task_runs_total{{status="error",{task}}} 1.0' in text
    assert f'michel_task_runs_total{{status="skipped",{task}}} 0.0' in text
    assert f"michel_task_last_success_timestamp_seconds{{{task}}} 1000.0" in text
    assert f"michel_task_last_failure_timestamp_seconds{{{task}}} 1100.0" in text
    assert 'michel_http_requests_total{host="www.googleapis.com",outcome="5xx"} 1.0' in text
    assert 'michel_background_task_failures_total{name="twitch-eventsub"} 1.0' in text
    assert "michel_backup_last_success_timestamp_seconds 1234.0" in text


def test_an_empty_registry_still_describes_every_series():
    text = generate_latest(_registry_with(HealthCollector(HealthRegistry().snapshot))).decode()
    assert "# TYPE michel_task_runs_total counter" in text
    values = [line for line in text.splitlines() if not line.startswith("#")]
    assert not [v for v in values if v.startswith("michel_backup")]  # no value yet


def _registry_with(collector):
    registry = CollectorRegistry()
    registry.register(collector)
    return registry


async def test_loop_lag_measures_a_blocked_loop():
    lags: list[float] = []
    watcher = asyncio.create_task(watch_loop_lag(lags.append, interval=0.02))
    await asyncio.sleep(0.03)
    time.sleep(0.1)  # something blocks the loop
    await asyncio.sleep(0.05)
    watcher.cancel()
    assert max(lags) >= 0.07
    assert min(lags) >= 0.0


# ── Error reporting ─────────────────────────────────────────────────────


def test_reporting_stays_off_without_a_dsn(monkeypatch):
    monkeypatch.setattr(error_reporting, "_enabled", False)
    assert error_reporting.init({}) is False
    assert error_reporting.init({"dsn": "  "}) is False
    error_reporting.capture(RuntimeError("ignored"))  # no-op
    assert not error_reporting.enabled()


def test_init_sends_no_personal_data_and_no_automatic_integration(monkeypatch):
    import sentry_sdk

    calls = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: calls.append(kw))
    monkeypatch.setattr(error_reporting, "_enabled", False)
    assert error_reporting.init({"dsn": "https://key@glitchtip.example/1"})
    (kwargs,) = calls
    assert kwargs["dsn"] == "https://key@glitchtip.example/1"
    assert kwargs["environment"] == "production"
    assert kwargs["send_default_pii"] is False
    assert kwargs["include_local_variables"] is False  # frames can hold tokens, message text
    assert kwargs["default_integrations"] is False
    assert kwargs["auto_enabling_integrations"] is False


def test_only_error_logs_with_an_exception_become_events():
    plain = record("API answered 503", logging.ERROR)
    assert error_reporting._before_send({}, {"log_record": plain}) is None

    try:
        raise RuntimeError("boom")
    except RuntimeError:
        import sys

        with log_context.scope(correlation_id="abc123", command="/bilan"):
            failing = record("failed", logging.ERROR, exc_info=sys.exc_info())
    event = error_reporting._before_send({}, {"log_record": failing})
    assert event["tags"] == {"correlation_id": "abc123", "command": "/bilan"}


def test_a_captured_exception_is_tagged_with_the_current_context():
    with log_context.scope(task="Poller.poll"):
        event = error_reporting._before_send({"tags": {"x": "1"}}, {})
    assert event["tags"] == {"x": "1", "task": "Poller.poll"}
