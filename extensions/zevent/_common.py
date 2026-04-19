"""Config, constants, and small shared helpers for the Zevent extension."""

import os
from dataclasses import dataclass
from datetime import UTC, datetime

from src.core import logging as logutil
from src.core.config import load_config
from src.webui.schemas import (
    SchemaBase,
    enabled_field,
    hidden_message_id,
    register_module,
    ui,
)

logger = logutil.init_logger(os.path.basename(__file__))


@register_module("moduleZevent")
class ZeventConfig(SchemaBase):
    __label__ = "Zevent"
    __description__ = "Suivi de l'événement Zevent en temps réel (dons, planning, streamers)."
    __icon__ = "🎉"
    __category__ = "Événements"

    enabled: bool = enabled_field()
    zeventChannelId: str = ui(
        "Salon",
        "channel",
        required=True,
        description="Salon où le message de suivi est posté (créé automatiquement).",
    )
    zeventPinMessage: bool = ui(
        "Épingler le message de suivi",
        "boolean",
        default=False,
        description="Épingler automatiquement le message de suivi.",
    )
    zeventMessageId: str | None = hidden_message_id("Message", "zeventChannelId")
    zeventStreamlabsApiUrl: str = ui(
        "URL Streamlabs",
        "url",
        description="URL de l'API Streamlabs Charity pour les dons.",
        default="https://streamlabscharity.com/api/v1/teams/@zevent-2025/zevent-2025",
    )
    zeventEventStartDate: str = ui(
        "Début de l'événement",
        "string",
        description=(
            "Date/heure de début du concert pré-événement "
            "(ISO 8601, ex: 2025-09-04T17:55:00+00:00)."
        ),
        default="2025-09-04T17:55:00+00:00",
    )
    zeventMainEventStartDate: str = ui(
        "Début du Zevent",
        "string",
        description="Date/heure de début du Zevent principal (ISO 8601).",
        default="2025-09-05T16:00:00+00:00",
    )
    zeventUpdateInterval: int = ui(
        "Intervalle de mise à jour (secondes)",
        "number",
        description="Fréquence de mise à jour du message en secondes. Nécessite un redémarrage.",
        default=30,
    )
    zeventMilestoneInterval: int = ui(
        "Intervalle des paliers (dons)",
        "number",
        description="Montant entre chaque notification de palier de dons.",
        default=100000,
    )


config, _module_config, _enabled_servers = load_config("moduleZevent")
_cfg = _module_config.get(_enabled_servers[0], {}) if _enabled_servers else {}


def _parse_event_dt(iso_str: str, default: datetime) -> datetime:
    try:
        return datetime.fromisoformat(iso_str) if iso_str else default
    except ValueError:
        return default


CHANNEL_ID = int(_cfg.get("zeventChannelId") or 0) or None
MESSAGE_ID = _cfg.get("zeventMessageId")
PIN_MESSAGE = bool(_cfg.get("zeventPinMessage", False))
GUILD_ID = _enabled_servers[0] if _enabled_servers else None

API_URL = "https://zevent.fr/api/"
PLANNING_API_URL = "https://zevent-api.gdoc.fr/events"
STREAMERS_API_URL = "https://zevent-api.gdoc.fr/streamers"
STREAMLABS_API_URL = _cfg.get(
    "zeventStreamlabsApiUrl",
    "https://streamlabscharity.com/api/v1/teams/@zevent-2025/zevent-2025",
)

UPDATE_INTERVAL = int(_cfg.get("zeventUpdateInterval", 30))
MILESTONE_INTERVAL = int(_cfg.get("zeventMilestoneInterval", 100000))

EVENT_START_DATE = _parse_event_dt(
    _cfg.get("zeventEventStartDate", ""),
    datetime(2025, 9, 4, 17, 55, 0, tzinfo=UTC),
)
MAIN_EVENT_START_DATE = _parse_event_dt(
    _cfg.get("zeventMainEventStartDate", ""),
    datetime(2025, 9, 5, 16, 0, 0, tzinfo=UTC),
)


@dataclass
class StreamerInfo:
    display_name: str
    twitch_name: str
    is_online: bool
    location: str


def split_streamer_list(streamer_list: str, max_length: int = 1024) -> list[str]:
    chunks = []
    current_chunk = []
    current_length = 0
    for streamer in streamer_list.split(", "):
        if current_length + len(streamer) + 2 > max_length:
            chunks.append(", ".join(current_chunk))
            current_chunk = [streamer]
            current_length = len(streamer)
        else:
            current_chunk.append(streamer)
            current_length += len(streamer) + 2

    if current_chunk:
        chunks.append(", ".join(current_chunk))

    return chunks
