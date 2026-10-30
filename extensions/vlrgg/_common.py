"""Shared constants, config, logger, dataclasses, and helpers for the vlrgg extension package."""

import json
import os
from dataclasses import dataclass, field
from typing import Any

from features.vlrgg import _clean_vlr_text, expand_round_name
from src.core import logging as logutil
from src.core.config import CONFIG_PATH, load_config
from src.discord_ext.embeds import Colors
from src.webui.schemas import SchemaBase, enabled_field, register_module, ui

# ── Module config ────────────────────────────────────────────────────────────


@register_module("moduleVlrgg")
class VlrggConfig(SchemaBase):
    __label__ = "Esport Tracker (VLR.gg)"
    __description__ = "Suivi automatique des matchs d'équipes Valorant via VLR.gg."
    __icon__ = "🎮"
    __category__ = "Esport & Jeux"

    enabled: bool = enabled_field()
    notificationChannelId: str | None = ui(
        "Salon notifications",
        "channel",
        description="Salon pour les notifications de matchs en direct et résultats.",
    )
    teams: list[Any] = ui(
        "Équipes suivies",
        "teams",
        description=(
            "Liste des équipes Valorant à suivre. Chaque équipe nécessite un nom et un ID VLR.gg."
        ),
    )
    reminderMinutes: int = ui(
        "Rappel avant match (minutes)",
        "number",
        default=15,
        description=(
            "Délai avant le début d'un match pour poster un rappel dans le salon de "
            "notifications (face-à-face récents, maps des deux équipes). 0 = désactivé."
        ),
    )


logger = logutil.init_logger(__name__)
config, module_config, enabled_servers = load_config("moduleVlrgg")

# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_EMBED_COLOR = Colors.VLR
LIVE_EMBED_COLOR = Colors.SUCCESS
MAX_PAST_MATCHES = 6
MAX_UPCOMING_MATCHES = 6
SCHEDULE_INTERVAL_MINUTES = 2
LIVE_UPDATE_INTERVAL_MINUTES = 0.5
DEFAULT_REMINDER_MINUTES = 15
# Fenêtre des bilans par map (rappels, /vlr equipe)
MAP_POOL_DAYS = 180
# Un match qui vient de finir peut encore s'afficher « LIVE » quelques secondes
# côté API : on retente le résumé final au cycle live suivant (30 s), au plus N fois.
FINALIZE_MAX_ATTEMPTS = 10

# ── Dataclasses ───────────────────────────────────────────────────────────────


@dataclass
class TeamConfig:
    """Configuration d'une équipe à suivre."""

    name: str
    vlr_team_id: str | None = None
    channel_id: str | None = None
    message_id: str | None = None
    pin: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TeamConfig":
        """Construit depuis un dict de config."""
        channel_id = None
        message_id = None
        cm = data.get("channelMessageId", "")
        if cm:
            if ":" in cm:
                parts = cm.split(":", 1)
                channel_id = parts[0].strip() or None
                message_id = parts[1].strip() or None
            else:
                channel_id = cm.strip() or None
        return cls(
            name=data.get("name", "Unknown"),
            vlr_team_id=data.get("vlrTeamId") or None,
            channel_id=channel_id,
            message_id=message_id,
            pin=bool(data.get("pin", False)),
        )


@dataclass
class PendingFinal:
    """Match terminé dont le message live attend le résumé définitif."""

    message: Any
    match_data: dict[str, Any]
    attempts: int = 0


@dataclass
class TeamState:
    """État de suivi d'une équipe."""

    team_config: TeamConfig
    server_id: str = ""
    schedule_message: Any = None
    notification_channel: Any = None
    reminder_minutes: int = DEFAULT_REMINDER_MINUTES
    ongoing_matches: dict[str, Any] = field(default_factory=dict)
    live_messages: dict[str, Any] = field(default_factory=dict)
    ending_matches: dict[str, PendingFinal] = field(default_factory=dict)


@dataclass
class ServerState:
    """État de suivi d'un serveur."""

    server_id: str
    notification_channel_id: str | None = None
    notification_channel: Any = None
    teams: dict[str, TeamState] = field(default_factory=dict)


# ── Config persistence ────────────────────────────────────────────────────────


def _save_team_channel_message(
    guild_id: str, team_name: str, channel_id: str, message_id: str
) -> None:
    """Update the channelMessageId for a specific team in config.json."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as file:
            data = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error("Could not read config for vlrgg team save: %s", e)
        return

    servers = data.setdefault("servers", {})
    guild = servers.setdefault(str(guild_id), {})
    mod = guild.setdefault("moduleVlrgg", {})
    teams = mod.setdefault("teams", [])
    combined = f"{channel_id}:{message_id}"
    for team in teams:
        if team.get("name") == team_name:
            team["channelMessageId"] = combined
            break
    else:
        teams.append({"name": team_name, "channelMessageId": combined})

    with open(CONFIG_PATH, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)
    logger.info("Saved channelMessageId for team %s on guild %s", team_name, guild_id)


# Re-export helpers that originate from features.vlrgg but are used across submodules
__all__ = [
    "VlrggConfig",
    "logger",
    "config",
    "module_config",
    "enabled_servers",
    "DEFAULT_EMBED_COLOR",
    "LIVE_EMBED_COLOR",
    "MAX_PAST_MATCHES",
    "MAX_UPCOMING_MATCHES",
    "SCHEDULE_INTERVAL_MINUTES",
    "LIVE_UPDATE_INTERVAL_MINUTES",
    "DEFAULT_REMINDER_MINUTES",
    "MAP_POOL_DAYS",
    "FINALIZE_MAX_ATTEMPTS",
    "PendingFinal",
    "TeamConfig",
    "TeamState",
    "ServerState",
    "_save_team_channel_message",
    "_clean_vlr_text",
    "expand_round_name",
]
