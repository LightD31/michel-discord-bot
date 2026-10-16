"""Text of the alert embeds — pure, so the extension only maps it onto an ``Embed``."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from features.uptime.models import AlertEvent, AlertMode
from src.integrations.uptime_kuma import MonitorStatus

# Discord's limit on an embed field value.
FIELD_VALUE_LIMIT = 1024


class AlertTone(StrEnum):
    """Which palette colour the extension gives the embed."""

    DOWN = "down"
    MAINTENANCE = "maintenance"
    UP = "up"


@dataclass(frozen=True)
class AlertField:
    name: str
    value: str
    inline: bool = True


@dataclass(frozen=True)
class AlertMessage:
    title: str
    description: str
    tone: AlertTone
    fields: list[AlertField] = field(default_factory=list)
    timestamp: datetime | None = None


# event -> (tone, simple title, simple description, detailed title, detailed description)
_TEXTS: dict[AlertEvent, tuple[AlertTone, str, str, str, str]] = {
    AlertEvent.MAINTENANCE: (
        AlertTone.MAINTENANCE,
        "🔧 Maintenance",
        "**{name}** est en maintenance",
        "🔧 Maintenance en cours",
        "Le capteur **{name}** est actuellement en maintenance.",
    ),
    AlertEvent.DOWN: (
        AlertTone.DOWN,
        "❌ Hors ligne",
        "**{name}** est hors ligne",
        "❌ Capteur hors ligne",
        "Le capteur **{name}** est actuellement hors ligne.",
    ),
    AlertEvent.MAINTENANCE_ENDED: (
        AlertTone.UP,
        "✅ Maintenance terminée",
        "**{name}** opérationnel",
        "✅ Fin de maintenance",
        "Le capteur **{name}** est de nouveau opérationnel après maintenance.",
    ),
    AlertEvent.RECOVERED: (
        AlertTone.UP,
        "✅ Rétabli",
        "**{name}** est en ligne",
        "✅ Capteur rétabli",
        "Le capteur **{name}** est de nouveau en ligne.",
    ),
}


def _clip(text: str, limit: int = FIELD_VALUE_LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_alert(
    event: AlertEvent,
    mode: AlertMode,
    *,
    monitor_id: int,
    monitor_name: str,
    previous: MonitorStatus | None,
    current: MonitorStatus,
    url: str | None = None,
    ping: float | None = None,
    at: datetime | None = None,
) -> AlertMessage:
    """Build the alert for *event*: title + status in ``simple`` mode, everything in ``detailed``."""
    tone, simple_title, simple_desc, detailed_title, detailed_desc = _TEXTS[event]
    if mode is AlertMode.SIMPLE:
        return AlertMessage(
            title=simple_title,
            description=simple_desc.format(name=monitor_name),
            tone=tone,
            fields=[AlertField("Statut", current.label)],
        )

    fields = [
        AlertField("ID du capteur", str(monitor_id)),
        AlertField("État actuel", current.label),
    ]
    if previous is not None and previous is not current:
        fields.append(AlertField("État précédent", previous.label))
    if url:
        fields.append(AlertField("URL", _clip(url), inline=False))
    if ping is not None:
        fields.append(AlertField("Ping", f"{ping:g} ms"))
    return AlertMessage(
        title=detailed_title,
        description=detailed_desc.format(name=monitor_name),
        tone=tone,
        fields=fields,
        timestamp=at,
    )
