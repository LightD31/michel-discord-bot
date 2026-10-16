"""Uptime Kuma client (no Discord imports).

The bot reads monitor statuses from Kuma's Prometheus endpoint, ``/metrics``,
authenticated with an API key (Kuma → Paramètres → Clés API). Such a key can
only read metrics — unlike the account password Kuma's Socket.IO API needs —
and ``/metrics`` can stay reachable while an authenticating reverse proxy
guards the dashboard. The other endpoint is ``/api/push/<token>``, the push
monitor that tracks the bot's own liveness.

Settings live in the global ``uptimeKuma`` config section and are read on
every call, so a dashboard edit takes effect without a restart.
``uptimeKumaUrl`` may be a full URL or a bare host, which is assumed to speak
HTTPS.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum
from typing import Any
from urllib.parse import quote, urlsplit

from aiohttp import ClientError, encode_basic_auth
from prometheus_client.parser import text_string_to_metric_families

from src.core import logging as logutil
from src.core.config import config_store
from src.core.errors import IntegrationError
from src.core.http import http_client

logger = logutil.init_logger(__name__)


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
        """Accept Kuma's number, a digit string, or a member name (legacy stored values)."""
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, float):
            if not value.is_integer():
                return None
            value = int(value)
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


@dataclass(frozen=True)
class MonitorState:
    """One monitor as ``/metrics`` reports it after its latest check."""

    id: int
    name: str
    url: str | None
    status: MonitorStatus
    response_time_ms: float | None = None


def _monitor_id(labels: Mapping[str, str]) -> int | None:
    value = labels.get("monitor_id", "").strip()
    return int(value) if value.isdigit() else None


def _display_url(value: str | None) -> str | None:
    """Non-HTTP monitors carry Kuma's ``https://`` placeholder (or ``null``) as their URL."""
    return value if value and urlsplit(value).netloc else None


def parse_metrics(text: str) -> dict[int, MonitorState]:
    """Monitor states from Kuma's ``/metrics`` exposition, keyed by monitor id.

    ``monitor_status`` gives each monitor's id, name, URL and status;
    ``monitor_response_time`` its last response time (``-1`` when the check
    had none). Paused and deleted monitors have no series at all, and neither
    has a monitor that hasn't been checked since Kuma started.
    """
    statuses: dict[int, tuple[Mapping[str, str], MonitorStatus]] = {}
    response_times: dict[int, float] = {}
    try:
        for family in text_string_to_metric_families(text):
            if family.name not in ("monitor_status", "monitor_response_time"):
                continue
            for sample in family.samples:
                monitor_id = _monitor_id(sample.labels)
                if monitor_id is None:
                    continue
                if family.name == "monitor_status":
                    status = MonitorStatus.parse(sample.value)
                    if status is not None:
                        statuses[monitor_id] = (sample.labels, status)
                elif math.isfinite(sample.value) and sample.value >= 0:
                    response_times[monitor_id] = sample.value
    except ValueError as e:
        raise UptimeKumaError("unreadable /metrics payload") from e

    return {
        monitor_id: MonitorState(
            id=monitor_id,
            name=labels.get("monitor_name") or f"ID {monitor_id}",
            url=_display_url(labels.get("monitor_url")),
            status=status,
            response_time_ms=response_times.get(monitor_id),
        )
        for monitor_id, (labels, status) in statuses.items()
    }


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
    api_key: str
    push_token: str

    @classmethod
    def from_section(cls, raw: Mapping[str, Any]) -> UptimeKumaSettings:
        def text(key: str) -> str:
            return str(raw.get(key) or "").strip()

        return cls(
            base_url=normalize_base_url(text("uptimeKumaUrl")),
            api_key=text("uptimeKumaApiKey"),
            push_token=text("uptimeKumaToken"),
        )

    @classmethod
    def load(cls) -> UptimeKumaSettings:
        section = config_store.get().get("config", {}).get("uptimeKuma")
        return cls.from_section(section if isinstance(section, Mapping) else {})

    @property
    def metrics_configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    @property
    def push_configured(self) -> bool:
        return bool(self.base_url and self.push_token)


async def fetch_monitor_states(settings: UptimeKumaSettings) -> dict[int, MonitorState]:
    """Read every monitor's state from ``/metrics``.

    Kuma ignores the Basic-auth username and checks the password as an API
    key. Redirects are not followed: one means a proxy wants a login first.
    """
    if not settings.metrics_configured:
        raise UptimeKumaError("URL or API key not configured")
    session = await http_client.session()
    try:
        async with session.get(
            f"{settings.base_url}/metrics",
            headers={"Authorization": encode_basic_auth("", settings.api_key)},
            allow_redirects=False,
        ) as response:
            if response.status in (301, 302, 303, 307, 308):
                raise UptimeKumaError(
                    f"/metrics redirected (HTTP {response.status}): a proxy in front of Kuma "
                    "must let it through"
                )
            if response.status == 401:
                raise UptimeKumaError(
                    "/metrics refused the API key (HTTP 401): wrong, expired or disabled key, "
                    "or Kuma's limit of 60 API requests a minute"
                )
            if response.status != 200:
                raise UptimeKumaError(f"/metrics answered HTTP {response.status}")
            text = await response.text()
    except (TimeoutError, ClientError) as e:
        raise UptimeKumaError(f"/metrics unreachable: {type(e).__name__}") from e
    return parse_metrics(text)


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


__all__ = [
    "MonitorState",
    "MonitorStatus",
    "UptimeKumaError",
    "UptimeKumaSettings",
    "fetch_monitor_states",
    "normalize_base_url",
    "parse_metrics",
    "push_heartbeat",
]
