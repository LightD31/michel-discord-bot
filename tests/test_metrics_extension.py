"""The Prometheus exporter: what a scrape returns, and the port freed on unload (T01).

Each test builds the extension's own ``CollectorRegistry`` (never the global
one), as a dashboard reload does.
"""

import asyncio
import socket
import urllib.request
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from prometheus_client import generate_latest

from extensions import metrics as metrics_ext
from extensions.metrics import MetricsExtension
from features.xp import XpRepository
from src.core.errors import DatabaseError

GUILD = "123456789012345678"


def make(bot=None):
    ext = object.__new__(MetricsExtension)
    MetricsExtension.__init__(ext, bot or fake_bot())
    return ext


def fake_bot(**overrides):
    bot = SimpleNamespace(
        latency=0.042,
        guilds=[object(), object()],
        ext={"Xp": object(), "Metrics": object()},
        is_ready=False,
        get_guild=lambda guild_id: None,
    )
    for name, value in overrides.items():
        setattr(bot, name, value)
    return bot


def scrape(ext) -> str:
    return generate_latest(ext.registry).decode()


def test_a_scrape_reads_the_bot_state():
    text = scrape(make())
    assert "michel_gateway_latency_seconds 0.042" in text
    assert "michel_guilds 2.0" in text
    assert 'michel_extension_loaded{extension="Xp"} 1.0' in text
    assert "process_" in text or "python_info" in text


def test_a_gateway_not_connected_yet_has_no_latency_and_a_broken_bot_no_series():
    text = scrape(make(fake_bot(latency=float("inf"))))
    assert "\nmichel_gateway_latency_seconds " not in text

    class Broken:
        latency = None

        @property
        def guilds(self):
            raise RuntimeError("not ready")

    text = scrape(make(Broken()))  # the scrape itself must not fail
    assert "\nmichel_guilds " not in text


def test_commands_are_counted_per_outcome():
    ext = make()
    ctx = SimpleNamespace(command=SimpleNamespace(resolved_name="rank"))
    asyncio.run(MetricsExtension.on_command_completion.callback(ext, SimpleNamespace(ctx=ctx)))
    asyncio.run(MetricsExtension.on_command_completion.callback(ext, SimpleNamespace(ctx=ctx)))
    asyncio.run(MetricsExtension.on_command_error.callback(ext, SimpleNamespace(ctx=object())))
    text = scrape(ext)
    assert 'michel_commands_total{command="rank",status="ok"} 2.0' in text
    assert 'michel_commands_total{command="unknown",status="error"} 1.0' in text


def test_a_guild_mongodb_cannot_read_is_counted_and_left_out():
    ext = make()

    async def failing():
        raise DatabaseError("down")

    assert asyncio.run(ext._guarded("xp", GUILD, failing())) == []
    assert 'michel_stats_refresh_failures_total{module="xp"} 1.0' in scrape(ext)


@pytest.mark.parametrize(
    ("settings", "expected"),
    [({}, 300), ({"refreshSeconds": "5"}, 60), ({"refreshSeconds": "x"}, 300)],
)
def test_the_refresh_interval_has_a_floor(settings, expected):
    assert (
        metrics_ext._int_setting(settings, "refreshSeconds", 300, metrics_ext.MIN_REFRESH_SECONDS)
        == expected
    )


class FakeTask:
    running = False

    def __init__(self):
        self.started = 0
        self.calls = 0

    def start(self):
        self.started += 1
        self.running = True

    async def __call__(self):
        self.calls += 1


def test_a_disabled_exporter_opens_no_port(store_config):
    store_config({"config": {"metrics": {"enabled": False}}})
    ext = make()
    ext.refresh_tick = FakeTask()
    asyncio.run(MetricsExtension.on_startup.callback(ext))
    assert ext._server is None and ext.refresh_tick.started == 0


def test_the_exporter_serves_metrics_then_frees_its_port(store_config):
    # A real free port: 0 isn't one here (the port setting is floored at 1).
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
    store_config({"config": {"metrics": {"enabled": True, "host": "127.0.0.1", "port": free}}})
    ext = make()
    ext.refresh_tick = FakeTask()

    async def lifecycle():
        await MetricsExtension.on_startup.callback(ext)
        await MetricsExtension.on_startup.callback(ext)  # a second Startup changes nothing
        port = ext._server.server_port
        assert port == free
        body = await asyncio.to_thread(
            lambda: urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5).read()
        )
        assert b"michel_guilds 2.0" in body
        assert ext.refresh_tick.started == 1 and ext.refresh_tick.calls == 2
        await ext.async_drop()
        return port

    asyncio.run(lifecycle())
    assert ext._server is None and ext._lag_watcher is None


@pytest.mark.mongo
async def test_a_refresh_publishes_each_enabled_guild_xp(mongo, store_config):
    store_config(
        {
            "config": {},
            "servers": {
                mongo.guild_id: {"moduleXp": {"enabled": True}, "serverName": "Le serveur"},
                "999": {"moduleXp": {"enabled": False}},
            },
        }
    )
    repo = XpRepository(mongo.guild_id)
    await repo.insert_new_user("7", 120, 0.0)
    await repo.insert_new_user("8", 30, 0.0)
    await repo.log_event("7", 120, 120, datetime.now(UTC))
    ext = make()

    await ext.refresh(top_n=1)

    text = scrape(ext)
    assert f'michel_xp_members{{guild="Le serveur",guild_id="{mongo.guild_id}"}} 2.0' in text
    assert "michel_xp_points" in text and 'source="message"' in text
    assert text.count("michel_xp_member_points{") == 1  # top_n
    assert 'guild_id="999"' not in text
