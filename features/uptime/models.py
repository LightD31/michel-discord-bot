"""Per-guild Uptime Kuma alerts and the status transitions that trigger them."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from src.integrations.uptime_kuma import MonitorStatus


class AlertMode(StrEnum):
    """How much an alert embed shows."""

    SIMPLE = "simple"
    DETAILED = "detailed"

    @property
    def label(self) -> str:
        return "Simple" if self is AlertMode.SIMPLE else "Détaillé"

    @classmethod
    def parse(cls, value: object) -> AlertMode:
        try:
            return cls(str(value))
        except ValueError:
            return cls.DETAILED


class AlertEvent(StrEnum):
    """A status change worth posting."""

    DOWN = "down"
    MAINTENANCE = "maintenance"
    RECOVERED = "recovered"
    MAINTENANCE_ENDED = "maintenance_ended"


@dataclass
class MonitorAlert:
    """One monitor watched in one guild.

    ``last_status`` is the last *settled* status seen for the monitor (never
    ``PENDING``); ``None`` until the first beat after setup gives a baseline.
    """

    monitor_id: int
    channel_id: int
    mode: AlertMode = AlertMode.DETAILED
    last_status: MonitorStatus | None = None

    @classmethod
    def from_setting(
        cls, entry: object, last_status: MonitorStatus | None = None
    ) -> MonitorAlert | None:
        """One entry of ``moduleUptime.alerts`` (P42); ``None`` when it can't be used."""
        if not isinstance(entry, Mapping):
            return None
        try:
            monitor_id = int(str(entry["monitorId"]))
            channel_id = int(str(entry["channelId"]))
        except (KeyError, TypeError, ValueError):
            return None
        return cls(monitor_id, channel_id, AlertMode.parse(entry.get("mode")), last_status)

    def to_setting(self) -> dict[str, Any]:
        return {
            "monitorId": str(self.monitor_id),
            "channelId": str(self.channel_id),
            "mode": self.mode.value,
        }


def alerts_from_settings(
    settings: Mapping[str, Any], statuses: Mapping[int, MonitorStatus | None]
) -> dict[int, MonitorAlert]:
    """The guild's alerts (its settings), each with the last status the bot saw."""
    alerts: dict[int, MonitorAlert] = {}
    for entry in settings.get("alerts") or []:
        alert = MonitorAlert.from_setting(entry)
        if alert is not None:
            alert.last_status = statuses.get(alert.monitor_id)
            alerts[alert.monitor_id] = alert
    return alerts


@dataclass(frozen=True)
class Transition:
    """Outcome of one beat: the settled status to keep and what to post, if anything."""

    status: MonitorStatus | None
    event: AlertEvent | None = None


def transition(previous: MonitorStatus | None, current: MonitorStatus) -> Transition:
    """Decide what a beat with *current* status means after *previous*.

    ``PENDING`` (Kuma retrying before declaring a monitor down) is ignored and
    leaves the settled status untouched. The first settled beat only sets the
    baseline. Coming back ``UP`` is posted only after ``DOWN`` or
    ``MAINTENANCE``. Older data may hold ``PENDING`` as the previous status:
    it counts as neither, so only a ``DOWN`` or ``MAINTENANCE`` beat posts.
    """
    if current is MonitorStatus.PENDING:
        return Transition(previous)
    if previous is None or previous is current:
        return Transition(current)
    if current is MonitorStatus.DOWN:
        return Transition(current, AlertEvent.DOWN)
    if current is MonitorStatus.MAINTENANCE:
        return Transition(current, AlertEvent.MAINTENANCE)
    if previous is MonitorStatus.DOWN:
        return Transition(current, AlertEvent.RECOVERED)
    if previous is MonitorStatus.MAINTENANCE:
        return Transition(current, AlertEvent.MAINTENANCE_ENDED)
    return Transition(current)


@dataclass(frozen=True)
class StatusChange:
    """An alert whose settled status just changed: persist it, and post ``event`` if set."""

    guild_id: str
    alert: MonitorAlert
    previous: MonitorStatus | None
    event: AlertEvent | None


def apply_statuses(
    alerts_by_guild: Mapping[str, Mapping[int, MonitorAlert]],
    statuses: Mapping[int, MonitorStatus],
) -> list[StatusChange]:
    """Advance every alert whose monitor reported a status; ``last_status`` is updated in place.

    Monitors missing from *statuses* (paused, deleted, or not checked since
    Kuma started) keep their alerts untouched, so they post nothing until
    they report again.
    """
    changes: list[StatusChange] = []
    for guild_id, alerts in alerts_by_guild.items():
        for alert in alerts.values():
            current = statuses.get(alert.monitor_id)
            if current is None:
                continue
            outcome = transition(alert.last_status, current)
            if outcome.status is alert.last_status:
                continue
            previous, alert.last_status = alert.last_status, outcome.status
            changes.append(StatusChange(guild_id, alert, previous, outcome.event))
    return changes
