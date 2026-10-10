"""Prometheus exporter.

Serves ``/metrics`` on its own port (global config section ``metrics``),
independent of the Web UI. Three groups of metrics:

- **XP and Spotify statistics** per guild, recomputed from MongoDB every
  ``refreshSeconds`` on the bot loop and published to a
  :class:`~features.metrics.SnapshotCollector` — scrapes never touch MongoDB;
- **bot health** (gateway latency, guilds, loaded extensions), read from the
  client at scrape time;
- **command invocations** per command and outcome, plus the standard
  ``process_*`` / ``python_*`` collectors.

Everything lives in a registry owned by this instance, so a dashboard reload
builds a fresh one instead of colliding with the old series in
``prometheus_client``'s global registry.
"""

import asyncio
import math
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from interactions import Client, Extension, IntervalTrigger, Task, listen
from interactions.api.events import CommandCompletion, CommandError
from prometheus_client import (
    CollectorRegistry,
    Counter,
    GCCollector,
    PlatformCollector,
    ProcessCollector,
    start_http_server,
)

from features.metrics import (
    ACTIVE_WINDOWS,
    SNAPSHOT_METRICS,
    MetricSpec,
    Sample,
    SnapshotCollector,
    spotify_samples,
    xp_samples,
)
from features.spotify import SpotifyRepository, aggregate
from features.userinfo import UserInfoRepository, member_label_resolver
from features.xp import XpRepository
from src.core import logging as logutil
from src.core.config import config_store
from src.core.errors import DatabaseError

logger = logutil.init_logger(__name__)

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 9108
DEFAULT_REFRESH_SECONDS = 300
DEFAULT_TOP_MEMBERS = 25
MIN_REFRESH_SECONDS = 60
UNKNOWN_MEMBER = "Membre inconnu"

LIVE_METRICS: tuple[MetricSpec, ...] = (
    MetricSpec("michel_gateway_latency_seconds", "Discord gateway heartbeat latency.", ()),
    MetricSpec("michel_guilds", "Guilds the bot is in.", ()),
    MetricSpec("michel_extension_loaded", "1 for each loaded extension.", ("extension",)),
    MetricSpec(
        "michel_stats_refresh_timestamp_seconds",
        "When the XP/Spotify statistics were last recomputed.",
        (),
    ),
    MetricSpec(
        "michel_stats_refresh_duration_seconds",
        "How long the last XP/Spotify recomputation took.",
        (),
    ),
)


def _settings() -> dict[str, Any]:
    section = config_store.get().get("config", {}).get("metrics", {})
    return section if isinstance(section, dict) else {}


def _int_setting(settings: dict[str, Any], key: str, default: int, minimum: int = 1) -> int:
    try:
        return max(int(settings.get(key, default)), minimum)
    except (TypeError, ValueError):
        return default


def _enabled_guilds(module_name: str) -> list[tuple[str, dict[str, Any]]]:
    """``(guild_id, server_config)`` for each guild where *module_name* is on."""
    servers = config_store.get().get("servers", {})
    return [
        (str(guild_id), server)
        for guild_id, server in servers.items()
        if isinstance(server, dict) and (server.get(module_name) or {}).get("enabled", False)
    ]


def _command_name(ctx: Any) -> str:
    command = getattr(ctx, "command", None)
    name = getattr(command, "resolved_name", None) or getattr(command, "name", None)
    return str(name) if name else "unknown"


class MetricsExtension(Extension):
    def __init__(self, bot: Client) -> None:
        self.bot: Client = bot
        self.registry = CollectorRegistry()
        self.collector = SnapshotCollector((*SNAPSHOT_METRICS, *LIVE_METRICS), self._live_samples)
        self.registry.register(self.collector)
        ProcessCollector(registry=self.registry)
        PlatformCollector(registry=self.registry)
        GCCollector(registry=self.registry)
        self.command_runs = Counter(
            "michel_commands",
            "Commands and component callbacks run, per outcome.",
            ("command", "status"),
            registry=self.registry,
        )
        self.refresh_failures = Counter(
            "michel_stats_refresh_failures",
            "Guild statistics that could not be recomputed.",
            ("module",),
            registry=self.registry,
        )
        self._server: Any = None
        self._last_refresh = 0.0
        self._refresh_timestamp: float | None = None
        self._refresh_duration: float | None = None
        self._refresh_lock = asyncio.Lock()

    # --- Lifecycle ----------------------------------------------------------

    @listen()
    async def on_startup(self) -> None:
        settings = _settings()
        if not settings.get("enabled", False):
            logger.info("Prometheus exporter disabled (config.metrics.enabled)")
            return
        if self._server is None:
            host = str(settings.get("host") or DEFAULT_HOST)
            port = _int_setting(settings, "port", DEFAULT_PORT)
            try:
                self._server, _ = start_http_server(port, addr=host, registry=self.registry)
            except OSError as e:
                logger.error("Prometheus exporter could not bind %s:%s: %s", host, port, e)
                return
            logger.info("Prometheus exporter listening on http://%s:%s/metrics", host, port)
        if not self.refresh_tick.running:
            self.refresh_tick.start()
        await self.refresh_tick()

    async def async_drop(self) -> None:
        """Dashboard unload/reload: free the port before a new instance binds it."""
        server, self._server = self._server, None
        if server is not None:
            await asyncio.to_thread(server.shutdown)
            server.server_close()

    # --- Commands -----------------------------------------------------------

    @listen(CommandCompletion)
    async def on_command_completion(self, event: CommandCompletion) -> None:
        self.command_runs.labels(_command_name(event.ctx), "ok").inc()

    @listen(CommandError)
    async def on_command_error(self, event: CommandError) -> None:
        self.command_runs.labels(_command_name(event.ctx), "error").inc()

    # --- Scrape-time samples ------------------------------------------------

    def _live_samples(self) -> list[Sample]:
        """Plain attribute reads only: this runs on the scrape thread.

        A failing read drops the bot series from this scrape rather than
        failing the whole scrape with a 500.
        """
        samples: list[Sample] = []
        try:
            latency = self.bot.latency  # None until the gateway connects
            if isinstance(latency, int | float) and math.isfinite(latency):
                samples.append(Sample("michel_gateway_latency_seconds", (), latency))
            samples.append(Sample("michel_guilds", (), len(self.bot.guilds or [])))
            for name in list(self.bot.ext):
                samples.append(Sample("michel_extension_loaded", (("extension", name),), 1))
        except Exception as e:  # noqa: BLE001 — never fail a scrape over bot state
            logger.debug("Metrics: could not read bot state: %s", e)
        if self._refresh_timestamp is not None:
            samples.append(
                Sample("michel_stats_refresh_timestamp_seconds", (), self._refresh_timestamp)
            )
        if self._refresh_duration is not None:
            samples.append(
                Sample("michel_stats_refresh_duration_seconds", (), self._refresh_duration)
            )
        return samples

    # --- Statistics refresh -------------------------------------------------

    @Task.create(IntervalTrigger(seconds=MIN_REFRESH_SECONDS))
    async def refresh_tick(self) -> None:
        """Recompute the statistics once ``refreshSeconds`` have passed.

        Ticks every minute and reads the interval from config each time, so a
        dashboard edit applies without reloading the extension.
        """
        settings = _settings()
        if not settings.get("enabled", False):
            return
        interval = _int_setting(
            settings, "refreshSeconds", DEFAULT_REFRESH_SECONDS, MIN_REFRESH_SECONDS
        )
        if self._refresh_lock.locked() or time.monotonic() - self._last_refresh < interval:
            return
        async with self._refresh_lock:
            started = time.monotonic()
            await self.refresh(_int_setting(settings, "topMembers", DEFAULT_TOP_MEMBERS))
            self._last_refresh = time.monotonic()
            self._refresh_duration = self._last_refresh - started
            self._refresh_timestamp = time.time()

    async def refresh(self, top_n: int) -> None:
        samples: list[Sample] = []
        for guild_id, server in _enabled_guilds("moduleXp"):
            samples.extend(await self._guarded("xp", guild_id, self._xp(guild_id, server, top_n)))
        for guild_id, server in _enabled_guilds("moduleSpotify"):
            samples.extend(
                await self._guarded("spotify", guild_id, self._spotify(guild_id, server, top_n))
            )
        self.collector.publish(samples)

    async def _guarded(self, module: str, guild_id: str, work: Any) -> list[Sample]:
        """One guild's samples, or none (logged and counted) if MongoDB fails."""
        try:
            return await work
        except DatabaseError as e:
            logger.warning("Metrics refresh failed for %s in guild %s: %s", module, guild_id, e)
            self.refresh_failures.labels(module).inc()
            return []

    def _guild_name(self, guild_id: str, server: dict[str, Any]) -> str:
        guild = self.bot.get_guild(guild_id) if self.bot.is_ready else None
        return str(getattr(guild, "name", None) or server.get("serverName") or guild_id)

    async def _xp(self, guild_id: str, server: dict[str, Any], top_n: int) -> list[Sample]:
        repo = XpRepository(guild_id)
        now = datetime.now(UTC)
        docs, source_totals, *active = await asyncio.gather(
            repo.list_all_sorted_by_xp(),
            repo.totals_by_source(),
            *(repo.count_active_users(now - timedelta(days=d)) for d in ACTIVE_WINDOWS.values()),
        )
        top_ids = [str(d.get("_id", "")) for d in docs[:top_n]]
        names = await UserInfoRepository(guild_id).get_names(top_ids)
        return xp_samples(
            guild_id,
            self._guild_name(guild_id, server),
            docs,
            names,
            dict(zip(ACTIVE_WINDOWS, active, strict=True)),
            source_totals,
            top_n,
        )

    async def _spotify(self, guild_id: str, server: dict[str, Any], top_n: int) -> list[Sample]:
        repo = SpotifyRepository(guild_id)
        playlist, votes, vote_infos = await asyncio.gather(
            repo.find_playlist_items({}), repo.find_vote_docs({}), repo.get_vote_infos()
        )
        stats = aggregate(
            playlist.values(),
            votes.values(),
            (vote_infos or {}).get("track_id"),
            top=top_n,
            top_voters=top_n,
        )
        names = await UserInfoRepository(guild_id).get_names(sorted(stats.user_ids()))
        label_of = member_label_resolver(server.get("discord2name"), names, UNKNOWN_MEMBER)
        return spotify_samples(guild_id, self._guild_name(guild_id, server), stats, label_of)


def setup(bot: Client) -> None:
    MetricsExtension(bot)
