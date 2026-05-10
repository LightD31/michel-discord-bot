"""Shared constants, config, dataclasses, and persistence helpers for the MDI extension."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from features.mdi import TeamRef
from src.core import logging as logutil
from src.core.config import CONFIG_PATH, load_config
from src.core.db import mongo_manager
from src.discord_ext.embeds import Colors
from src.webui.schemas import SchemaBase, enabled_field, register_module, ui

# ── Module config (Web UI schema) ─────────────────────────────────────────────

MODULE_KEY = "moduleMDI"
DEFAULT_EVENT_SLUG = "mdi-midnight-season-1"
DEFAULT_TEAM_SLUG = "mandatory"


@register_module(MODULE_KEY)
class MDIConfig(SchemaBase):
    __label__ = "MDI Tracker (Raider.IO)"
    __description__ = (
        "Suivi automatique du tournoi Mythic Dungeon International via Raider.IO "
        "(par défaut équipe Mandatory)."
    )
    __icon__ = "🐉"
    __category__ = "Esport & Jeux"

    enabled: bool = enabled_field()
    notificationChannelId: str | None = ui(
        "Salon notifications",
        "channel",
        description="Salon où sont publiés le planning épinglé et les messages de match.",
    )
    scheduleChannelMessageId: str | None = ui(
        "Message du planning (channelId:messageId)",
        "string",
        description=(
            "Géré automatiquement par le bot. Vider pour forcer une nouvelle publication "
            "lors du prochain cycle."
        ),
    )
    eventSlug: str = ui(
        "Slug de l'événement",
        "string",
        default=DEFAULT_EVENT_SLUG,
        description="Slug Raider.IO de l'événement (ex: mdi-midnight-season-1).",
    )
    teamSlug: str = ui(
        "Slug de l'équipe suivie",
        "string",
        default=DEFAULT_TEAM_SLUG,
        description="Slug Raider.IO de l'équipe à suivre.",
    )
    pinSchedule: bool = ui(
        "Épingler le planning",
        "boolean",
        default=True,
        description="Épingle le message de planning au salon de notifications.",
    )
    pingRoleId: str | None = ui(
        "Rôle à mentionner",
        "role",
        description="Rôle mentionné quand un nouveau match commence (optionnel).",
    )


logger = logutil.init_logger(os.path.basename(__file__))
config, module_config, enabled_servers = load_config("moduleMDI")


# ── Constants ────────────────────────────────────────────────────────────────

SCHEDULE_INTERVAL_MINUTES = 5.0
LIVE_INTERVAL_MINUTES = 1.0

EMBED_COLOR_DEFAULT = Colors.UTIL
EMBED_COLOR_SCHEDULED = Colors.WARNING
EMBED_COLOR_LIVE = Colors.ERROR
EMBED_COLOR_WIN = Colors.SUCCESS
EMBED_COLOR_LOSS = 0x8B0000  # darker red for "Mandatory lost"

STATUS_EMOJI_SCHEDULED = "🟡"
STATUS_EMOJI_LIVE = "🔴"
STATUS_EMOJI_TERMINAL_WIN = "🏆"
STATUS_EMOJI_TERMINAL_LOSS = "❌"
STATUS_EMOJI_TERMINAL_NEUTRAL = "🏁"

RAIDERIO_ICON_URL = "https://cdn.raiderio.net/images/brand/icon-180.png"


# ── Dataclasses ───────────────────────────────────────────────────────────────


@dataclass
class GuildConfig:
    """Parsed per-guild module config."""

    notification_channel_id: str | None
    schedule_channel_id: str | None
    schedule_message_id: str | None
    event_slug: str
    team_slug: str
    pin_schedule: bool
    ping_role_id: str | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GuildConfig:
        notif = (data.get("notificationChannelId") or "") or None
        sched_cm = data.get("scheduleChannelMessageId") or ""
        sched_channel: str | None = None
        sched_message: str | None = None
        if sched_cm:
            if ":" in sched_cm:
                ch, msg = sched_cm.split(":", 1)
                sched_channel = ch.strip() or None
                sched_message = msg.strip() or None
            else:
                sched_channel = sched_cm.strip() or None
        return cls(
            notification_channel_id=notif,
            schedule_channel_id=sched_channel or notif,
            schedule_message_id=sched_message,
            event_slug=(data.get("eventSlug") or DEFAULT_EVENT_SLUG).strip() or DEFAULT_EVENT_SLUG,
            team_slug=(data.get("teamSlug") or DEFAULT_TEAM_SLUG).strip().lower()
            or DEFAULT_TEAM_SLUG,
            pin_schedule=bool(data.get("pinSchedule", True)),
            ping_role_id=(data.get("pingRoleId") or "") or None,
        )


@dataclass
class GuildState:
    """In-memory state for one guild."""

    server_id: str
    guild_config: GuildConfig
    notification_channel: Any = None
    schedule_channel: Any = None
    schedule_message: Any = None
    tracked_team: TeamRef | None = None
    schedule_last_hash: str | None = None
    matches: dict[int, dict[str, Any]] = field(default_factory=dict)
    """match_id -> persisted document fields {channel_id, message_id, last_hash, terminal, ...}"""


# ── Mongo helpers ─────────────────────────────────────────────────────────────


def matches_col(server_id: str):
    """Return the per-guild collection storing per-match message metadata."""
    return mongo_manager.get_guild_collection(server_id, "mdi_matches")


# ── Config persistence ────────────────────────────────────────────────────────


def save_schedule_channel_message_id(
    guild_id: str, channel_id: str | None, message_id: str | None
) -> None:
    """Persist ``scheduleChannelMessageId`` on disk so it survives restarts.

    Mirrors the pattern used by the vlrgg extension. Writing through the raw
    ``CONFIG_PATH`` is acceptable here because ``ConfigStore`` re-reads the file
    on next access; the field is editable from the dashboard, so the next read
    picks up the bot's update.
    """
    try:
        with open(CONFIG_PATH, encoding="utf-8") as file:
            data = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error("Could not read config to persist MDI schedule message: %s", e)
        return

    servers = data.setdefault("servers", {})
    guild = servers.setdefault(str(guild_id), {})
    mod = guild.setdefault(MODULE_KEY, {})
    if channel_id and message_id:
        mod["scheduleChannelMessageId"] = f"{channel_id}:{message_id}"
    elif channel_id:
        mod["scheduleChannelMessageId"] = channel_id
    else:
        mod["scheduleChannelMessageId"] = ""

    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=4, ensure_ascii=False)
    except OSError as e:
        logger.error("Could not write MDI schedule message id to config: %s", e)
        return
    logger.info(
        "Saved MDI schedule message id for guild %s: %s",
        guild_id,
        mod["scheduleChannelMessageId"],
    )


__all__ = [
    "DEFAULT_EVENT_SLUG",
    "DEFAULT_TEAM_SLUG",
    "EMBED_COLOR_DEFAULT",
    "EMBED_COLOR_LIVE",
    "EMBED_COLOR_LOSS",
    "EMBED_COLOR_SCHEDULED",
    "EMBED_COLOR_WIN",
    "GuildConfig",
    "GuildState",
    "LIVE_INTERVAL_MINUTES",
    "MDIConfig",
    "MODULE_KEY",
    "RAIDERIO_ICON_URL",
    "SCHEDULE_INTERVAL_MINUTES",
    "STATUS_EMOJI_LIVE",
    "STATUS_EMOJI_SCHEDULED",
    "STATUS_EMOJI_TERMINAL_LOSS",
    "STATUS_EMOJI_TERMINAL_NEUTRAL",
    "STATUS_EMOJI_TERMINAL_WIN",
    "config",
    "enabled_servers",
    "logger",
    "matches_col",
    "module_config",
    "save_schedule_channel_message_id",
]
