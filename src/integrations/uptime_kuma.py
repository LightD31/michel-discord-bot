"""Uptime Kuma client (no Discord imports).

Uptime Kuma has no REST API for monitors: its own frontend talks to the server
over Socket.IO, and so does this client. After a ``login`` the server pushes
the monitor list (``monitorList``), the recent beats of every monitor
(``heartbeatList``) and then one ``heartbeat`` event per check. The only REST
endpoint the bot uses is ``/api/push/<token>``, the push monitor that tracks
the bot's own liveness.

Settings live in the global ``uptimeKuma`` config section and are read on
every (re)connection and push, so a dashboard edit takes effect without a
restart. ``uptimeKumaUrl`` may be a full URL (``http://uptime-kuma:3001``, to
reach the instance on a Docker network and bypass an authenticating reverse
proxy) or a bare host, which is assumed to speak HTTPS.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import IntEnum
from typing import Any
from urllib.parse import quote, urlsplit

import socketio
from aiohttp import ClientError

from src.core import logging as logutil
from src.core.config import config_store
from src.core.errors import IntegrationError
from src.core.http import http_client
from src.core.tasks import spawn

logger = logutil.init_logger(__name__)

# Seconds to wait before rechecking an unset section, and the bounds of the
# backoff between failed connection attempts. python-socketio's own
# reconnection (after an established link drops) uses the same ceiling.
IDLE_RECHECK_SECONDS = 300
RECONNECT_DELAY_MIN = 5
RECONNECT_DELAY_MAX = 300
LOGIN_TIMEOUT_SECONDS = 30
# Kuma registers a socket's event handlers only after greeting it with
# ``info``; a ``login`` sent before that is silently dropped.
GREETING_TIMEOUT_SECONDS = 10


class UptimeKumaError(IntegrationError):
    """Raised when an Uptime Kuma call fails."""


class MonitorStatus(IntEnum):
    """Uptime Kuma's numeric heartbeat status."""

    DOWN = 0
    UP = 1
    PENDING = 2
    MAINTENANCE = 3

    @property
    def label(self) -> str:
        return _STATUS_LABELS[self]

    @classmethod
    def parse(cls, value: object) -> MonitorStatus | None:
        """Accept Kuma's int, a digit string, or a member name (legacy stored values)."""
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, int):
            return cls(value) if value in cls._value2member_map_ else None
        if isinstance(value, str):
            text = value.strip()
            if text.isdigit():
                return cls.parse(int(text))
            return cls.__members__.get(text.upper())
        return None


_STATUS_LABELS = {
    MonitorStatus.DOWN: "Hors ligne",
    MonitorStatus.UP: "En ligne",
    MonitorStatus.PENDING: "En attente",
    MonitorStatus.MAINTENANCE: "Maintenance",
}


def _parse_id(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None


def _parse_time(value: object) -> datetime | None:
    """Kuma stores beat times in UTC as ``YYYY-MM-DD HH:MM:SS.mmm``."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


@dataclass(frozen=True)
class Monitor:
    """The parts of a Kuma monitor the bot displays."""

    id: int
    name: str
    path_name: str
    url: str | None

    @property
    def display_name(self) -> str:
        """``Groupe / Moniteur`` for a monitor inside a group, else its name."""
        return self.path_name or self.name

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> Monitor | None:
        monitor_id = _parse_id(payload.get("id"))
        if monitor_id is None:
            return None
        name = str(payload.get("name") or f"ID {monitor_id}")
        url = payload.get("url")
        # Non-HTTP monitors keep Kuma's "https://" placeholder in ``url``.
        has_url = isinstance(url, str) and bool(urlsplit(url).netloc)
        return cls(
            id=monitor_id,
            name=name,
            path_name=str(payload.get("pathName") or ""),
            url=url if has_url else None,
        )


@dataclass(frozen=True)
class Heartbeat:
    """One check result. Live beats say ``monitorID``, history rows ``monitor_id``."""

    monitor_id: int
    status: MonitorStatus
    time: datetime | None
    msg: str
    ping: float | None

    @classmethod
    def from_payload(
        cls, payload: Mapping[str, Any], monitor_id: int | None = None
    ) -> Heartbeat | None:
        if monitor_id is None:
            monitor_id = _parse_id(payload.get("monitorID", payload.get("monitor_id")))
        status = MonitorStatus.parse(payload.get("status"))
        if monitor_id is None or status is None:
            return None
        ping = payload.get("ping")
        return cls(
            monitor_id=monitor_id,
            status=status,
            time=_parse_time(payload.get("time")),
            msg=str(payload.get("msg") or ""),
            ping=float(ping)
            if isinstance(ping, int | float) and not isinstance(ping, bool)
            else None,
        )


def latest_heartbeat(monitor_id: object, rows: object) -> Heartbeat | None:
    """The newest beat of a ``heartbeatList`` event (rows come oldest first)."""
    parsed_id = _parse_id(monitor_id)
    if parsed_id is None or not isinstance(rows, list):
        return None
    for row in reversed(rows):
        if isinstance(row, Mapping):
            beat = Heartbeat.from_payload(row, parsed_id)
            if beat is not None:
                return beat
    return None


def normalize_base_url(value: str) -> str:
    """``status.example.com`` → ``https://status.example.com``; full URLs kept as-is."""
    text = value.strip().rstrip("/")
    if text and "://" not in text:
        text = f"https://{text}"
    return text


@dataclass(frozen=True)
class UptimeKumaSettings:
    """Snapshot of the global ``uptimeKuma`` config section."""

    base_url: str
    username: str
    password: str
    two_factor_code: str
    push_token: str

    @classmethod
    def from_section(cls, raw: Mapping[str, Any]) -> UptimeKumaSettings:
        def text(key: str) -> str:
            return str(raw.get(key) or "").strip()

        return cls(
            base_url=normalize_base_url(text("uptimeKumaUrl")),
            username=text("uptimeKumaUsername"),
            password=str(raw.get("uptimeKumaPassword") or ""),
            two_factor_code=text("uptimeKuma2FA"),
            push_token=text("uptimeKumaToken"),
        )

    @classmethod
    def load(cls) -> UptimeKumaSettings:
        section = config_store.get().get("config", {}).get("uptimeKuma")
        return cls.from_section(section if isinstance(section, Mapping) else {})

    @property
    def socket_configured(self) -> bool:
        return bool(self.base_url and self.username and self.password)

    @property
    def push_configured(self) -> bool:
        return bool(self.base_url and self.push_token)


async def push_heartbeat(settings: UptimeKumaSettings, ping_ms: float | None) -> None:
    """Report the bot as up to its Kuma push monitor.

    Errors never include the URL: the push token is part of its path.
    """
    if not settings.push_configured:
        raise UptimeKumaError("push monitor not configured")
    url = f"{settings.base_url}/api/push/{quote(settings.push_token, safe='')}"
    params = {"status": "up", "msg": "OK"}
    if ping_ms is not None and math.isfinite(ping_ms):
        params["ping"] = f"{ping_ms:.1f}"
    session = await http_client.session()
    try:
        async with session.get(url, params=params) as response:
            if response.status == 200:
                return
            body = await response.text()
    except (TimeoutError, ClientError) as e:
        raise UptimeKumaError(f"push failed: {type(e).__name__}") from e
    raise UptimeKumaError(f"push refused (HTTP {response.status}): {body[:200]}")


HeartbeatHandler = Callable[[Heartbeat], Awaitable[None]]


class UptimeKumaClient:
    """One Socket.IO session with Uptime Kuma, kept alive until :meth:`close`.

    ``on_heartbeat`` runs for every live beat and, after each login, for the
    newest beat of every monitor — so a transition that happened while the
    link was down is still delivered once it comes back. Beats older than the
    last one delivered for the same monitor are dropped.
    """

    def __init__(self, on_heartbeat: HeartbeatHandler) -> None:
        self._on_heartbeat = on_heartbeat
        self._sio = socketio.AsyncClient(
            handle_sigint=False,
            reconnection_delay=RECONNECT_DELAY_MIN,
            reconnection_delay_max=RECONNECT_DELAY_MAX,
        )
        self._task: asyncio.Task[None] | None = None
        self._closed = False
        self._authenticated = False
        self._link_failing = False
        self._greeted = asyncio.Event()
        self.monitors: dict[int, Monitor] = {}
        self._latest: dict[int, Heartbeat] = {}

        self._sio.on("connect", self._on_connect)
        self._sio.on("disconnect", self._on_disconnect)
        self._sio.on("connect_error", self._on_connect_error)
        self._sio.on("info", self._on_info)
        self._sio.on("monitorList", self._on_monitor_list)
        self._sio.on("updateMonitorIntoList", self._on_update_monitors)
        self._sio.on("deleteMonitorFromList", self._on_delete_monitor)
        self._sio.on("heartbeatList", self._on_heartbeat_list)
        self._sio.on("heartbeat", self._on_heartbeat_event)

    # --- State --------------------------------------------------------------

    @property
    def ready(self) -> bool:
        """Logged in, so ``monitors`` and the beats reflect the server."""
        return self._sio.connected and self._authenticated

    @property
    def needs_login(self) -> bool:
        """Connected, but the last login was refused or never answered."""
        return self._sio.connected and not self._authenticated

    def latest_status(self, monitor_id: int) -> MonitorStatus | None:
        beat = self._latest.get(monitor_id)
        return beat.status if beat else None

    # --- Lifecycle ----------------------------------------------------------

    def start(self) -> None:
        if self._closed or (self._task is not None and not self._task.done()):
            return
        self._task = spawn(self._run(), name="uptime-kuma-socketio", log=logger)

    async def close(self) -> None:
        """Stop reconnecting and drop the link; the instance can't be restarted."""
        self._closed = True
        # Shut down before cancelling: ``_run`` may be awaiting the Engine.IO
        # read loop through ``wait()``, and cancelling it there would cancel
        # that loop and make ``shutdown()`` raise CancelledError.
        try:
            await self._sio.shutdown()
        except Exception as e:
            logger.warning("Uptime Kuma: Socket.IO shutdown failed: %s", e)
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._authenticated = False

    async def _run(self) -> None:
        delay = RECONNECT_DELAY_MIN
        unset_logged = False
        while not self._closed:
            settings = UptimeKumaSettings.load()
            if not settings.socket_configured:
                if not unset_logged:
                    logger.info(
                        "Uptime Kuma: URL, username or password unset, real-time alerts disabled"
                    )
                    unset_logged = True
                await asyncio.sleep(IDLE_RECHECK_SECONDS)
                continue
            unset_logged = False
            try:
                await self._sio.connect(
                    settings.base_url, transports=["websocket"], wait_timeout=10
                )
            except (socketio.exceptions.ConnectionError, ValueError) as e:
                self._report_failure(f"cannot reach {settings.base_url}: {e}")
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_DELAY_MAX)
                continue
            delay = RECONNECT_DELAY_MIN
            if self._closed:
                # close() ran while this connection was being set up.
                await self._sio.disconnect()
                return
            # Returns once the link is closed for good: python-socketio itself
            # reconnects a dropped link, but not one the server disconnected.
            await self._sio.wait()

    def _report_failure(self, message: str) -> None:
        """Warn once per outage, then stay quiet until the link recovers."""
        if self._link_failing:
            logger.debug("Uptime Kuma: %s", message)
        else:
            logger.warning("Uptime Kuma: %s (further failures logged at debug level)", message)
            self._link_failing = True

    # --- Authentication -----------------------------------------------------

    async def login(self) -> bool:
        """Log in with the current settings; Kuma then pushes monitors and beats."""
        settings = UptimeKumaSettings.load()
        try:
            response = await self._sio.call(
                "login",
                {
                    "username": settings.username,
                    "password": settings.password,
                    "token": settings.two_factor_code,
                },
                timeout=LOGIN_TIMEOUT_SECONDS,
            )
        except (socketio.exceptions.TimeoutError, socketio.exceptions.BadNamespaceError) as e:
            logger.error("Uptime Kuma: login got no answer: %s", type(e).__name__)
            return False
        if isinstance(response, Mapping) and response.get("ok"):
            self._authenticated = True
            if self._link_failing:
                logger.info("Uptime Kuma: link restored")
            self._link_failing = False
            logger.info("Uptime Kuma: logged in, %d monitors", len(self.monitors))
            return True
        if isinstance(response, Mapping) and response.get("tokenRequired"):
            logger.error("Uptime Kuma: the account requires a 2FA code (uptimeKuma2FA)")
        else:
            reason = response.get("msg") if isinstance(response, Mapping) else response
            logger.error("Uptime Kuma: login refused: %s", reason)
        return False

    # --- Socket.IO events ---------------------------------------------------

    async def _on_connect(self) -> None:
        self._authenticated = False
        # Not awaited here: the ack of ``login`` has to be read by the same
        # connection that is still dispatching this event.
        spawn(self._login_once_greeted(), name="uptime-kuma-login", log=logger)

    async def _login_once_greeted(self) -> None:
        # The greeting is cleared on disconnect, not here: ``info`` may be
        # dispatched before this connect handler runs.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._greeted.wait(), GREETING_TIMEOUT_SECONDS)
        await self.login()

    async def _on_info(self, *_: object) -> None:
        self._greeted.set()

    async def _on_disconnect(self, reason: object = None) -> None:
        self._authenticated = False
        self._greeted.clear()
        if not self._closed:
            self._report_failure(f"link lost ({reason})")

    async def _on_connect_error(self, data: object = None) -> None:
        self._report_failure(f"connection error: {data}")

    async def _on_monitor_list(self, data: object) -> None:
        if not isinstance(data, Mapping):
            return
        monitors = (Monitor.from_payload(m) for m in data.values() if isinstance(m, Mapping))
        self.monitors = {m.id: m for m in monitors if m is not None}

    async def _on_update_monitors(self, data: object) -> None:
        if not isinstance(data, Mapping):
            return
        for payload in data.values():
            monitor = Monitor.from_payload(payload) if isinstance(payload, Mapping) else None
            if monitor is not None:
                self.monitors[monitor.id] = monitor

    async def _on_delete_monitor(self, monitor_id: object) -> None:
        parsed = _parse_id(monitor_id)
        if parsed is not None:
            self.monitors.pop(parsed, None)
            self._latest.pop(parsed, None)

    async def _on_heartbeat_list(self, monitor_id: object, rows: object, *_: object) -> None:
        beat = latest_heartbeat(monitor_id, rows)
        if beat is not None:
            await self._dispatch(beat)

    async def _on_heartbeat_event(self, data: object) -> None:
        beat = Heartbeat.from_payload(data) if isinstance(data, Mapping) else None
        if beat is not None:
            await self._dispatch(beat)

    async def _dispatch(self, beat: Heartbeat) -> None:
        previous = self._latest.get(beat.monitor_id)
        if previous and previous.time and beat.time and beat.time < previous.time:
            return
        self._latest[beat.monitor_id] = beat
        try:
            await self._on_heartbeat(beat)
        except Exception:
            logger.exception(
                "Uptime Kuma: heartbeat handler failed for monitor %s", beat.monitor_id
            )


__all__ = [
    "Heartbeat",
    "Monitor",
    "MonitorStatus",
    "UptimeKumaClient",
    "UptimeKumaError",
    "UptimeKumaSettings",
    "latest_heartbeat",
    "normalize_base_url",
    "push_heartbeat",
]
