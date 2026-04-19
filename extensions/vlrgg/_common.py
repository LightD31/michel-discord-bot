"""Shared constants, config, logger, dataclasses, and helpers for the vlrgg extension package."""

import json
import os
from dataclasses import dataclass, field
from typing import Any

from src.core import logging as logutil
from src.core.config import CONFIG_PATH, load_config
from src.core.db import mongo_manager
from src.discord_ext.embeds import Colors
from src.vlrgg import _clean_vlr_text, expand_round_name
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


logger = logutil.init_logger(os.path.basename(__file__))
config, module_config, enabled_servers = load_config("moduleVlrgg")

# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_EMBED_COLOR = Colors.VLR
LIVE_EMBED_COLOR = Colors.SUCCESS
MAX_PAST_MATCHES = 6
MAX_UPCOMING_MATCHES = 6
SCHEDULE_INTERVAL_MINUTES = 2
LIVE_UPDATE_INTERVAL_MINUTES = 0.5

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
class TeamState:
    """État de suivi d'une équipe."""

    team_config: TeamConfig
    server_id: str = ""
    schedule_message: Any = None
    notification_channel: Any = None
    ongoing_matches: dict[str, Any] = field(default_factory=dict)
    live_messages: dict[str, Any] = field(default_factory=dict)


@dataclass
class ServerState:
    """État de suivi d'un serveur."""

    server_id: str
    notification_channel_id: str | None = None
    notification_channel: Any = None
    teams: dict[str, TeamState] = field(default_factory=dict)


# ── Mongo helpers ─────────────────────────────────────────────────────────────


def live_col(server_id: str):
    """Return the MongoDB collection for live match persistence."""
    return mongo_manager.get_guild_collection(server_id, "vlrgg_live")


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


# Re-export helpers that originate from src.vlrgg but are used across submodules
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
    "TeamConfig",
    "TeamState",
    "ServerState",
    "live_col",
    "_save_team_channel_message",
    "_clean_vlr_text",
    "expand_round_name",
]
