"""Shared config schema, logger, per-guild state and base command for the zunivers extension.

Config key ``moduleZunivers`` (formerly ``moduleColoc``). Also holds the
``/zunivers`` base command every mixin hangs its subcommands on.
"""

from dataclasses import dataclass, field
from datetime import datetime

from interactions import SlashCommand

from features.coloc import EventState, ReminderCollection, StorageManager
from features.zerator247 import RequestSubscription
from src.core import logging as logutil
from src.core.config import ModuleConfig, global_config
from src.discord_ext.scopes import guild_scopes
from src.webui.schemas import SchemaBase, enabled_field, register_module, register_section, ui


@register_section("zunivers")
class ZuniversSection(SchemaBase):
    """ZUnivers itself: the same site and API for every guild."""

    __label__ = "ZUnivers"
    __icon__ = "🎲"

    apiUrl: str = ui(
        "URL de l'API",
        "url",
        required=True,
        description=(
            "API publique de ZUnivers (https://zunivers-api.zerator.com/public). Vide = "
            "ni rappels de loot, ni annonces d'événements, ni récap corporation."
        ),
    )
    webUrl: str | None = ui(
        "URL du site",
        "url",
        description=(
            "Site de ZUnivers (https://zunivers.zerator.com) : lien du calendrier festif "
            "dans les rappels et du titre du récap corporation. Vide = aucun lien."
        ),
    )


@register_module("moduleZunivers")
class ZuniversConfig(SchemaBase):
    __label__ = "Zunivers"
    __description__ = (
        "Rappels /journa et !request ZeratoR 24/7, événements, récap corporation et "
        "conseils ZUnivers Ninja."
    )
    __icon__ = "🎲"
    __category__ = "Communauté"

    enabled: bool = enabled_field()
    colocZuniversChannelId: str | None = ui(
        "Salon Zunivers", "channel", description="Salon pour les notifications Zunivers."
    )
    journaReminderRoleId: str | None = ui(
        "Rôle rappel /journa",
        "role",
        description="Rôle mentionné quand le /journa quotidien n'a pas été posté à 22h.",
        role_use="mention",
    )
    journaNormalLink: str | None = ui(
        "Lien du salon /journa",
        "url",
        description=(
            "Lien Discord vers le salon où lancer /journa. Vide = le rappel "
            "affiche `/journa` sans lien."
        ),
    )
    journaHardcoreLink: str | None = ui(
        "Lien du salon /journa hardcore",
        "url",
        description=(
            "Lien Discord vers le salon où lancer /journa en mode hardcore. "
            "Vide = le rappel hardcore n'affiche pas de lien."
        ),
    )
    zuniversHardcoreImageUrl: str | None = ui(
        "Image saison hardcore",
        "url",
        description="Image affichée dans l'embed de saison hardcore. Vide = aucune image.",
    )
    zerator247Url: str | None = ui(
        "Site ZeratoR 24/7",
        "url",
        description=(
            "Adresse du site ZeratoR 24/7 (ex. https://247.zerator.com), dont l'API indique "
            "quand les !request s'ouvrent. Vide = rappels /zunivers 247 désactivés."
        ),
    )
    zerator247TwitchLogin: str | None = ui(
        "Chaîne Twitch ZeratoR 24/7",
        "string",
        default="zerator_247",
        description="Login de la chaîne Twitch où taper !request, lié dans les rappels.",
    )


logger = logutil.init_logger("extensions.zunivers")

ZUNIVERS = ModuleConfig("moduleZunivers")


def zunivers_config() -> dict:
    """The global ``zunivers`` section (ZUnivers' site and API), read at use."""
    section = global_config().get("zunivers")
    return section if isinstance(section, dict) else {}


def zunivers_api_url() -> str:
    """The ZUnivers public API base URL; empty when unset."""
    return str(zunivers_config().get("apiUrl") or "").rstrip("/")


def zunivers_web_url() -> str | None:
    """The ZUnivers site, for links; ``None`` when unset."""
    return zunivers_config().get("webUrl") or None


def ninja_config() -> dict:
    """The global ``zuniversNinja`` section (one server for the whole bot), read at use."""
    section = global_config().get("zuniversNinja")
    return section if isinstance(section, dict) else {}


@dataclass
class GuildState:
    """What the extension keeps for one guild, loaded from its database on first use."""

    guild_id: str
    storage: StorageManager
    reminders: ReminderCollection
    event_state: EventState
    request_subscriptions: dict[str, RequestSubscription] = field(default_factory=dict)
    # End time of the last 24/7 cycle announced, so one requests window is announced once.
    last_request_cycle_end: datetime | None = None


zunivers_cmd = SlashCommand(
    name="zunivers",
    description="Commandes ZUnivers",
    scopes=guild_scopes(ZUNIVERS.guild_ids()),
)

__all__ = [
    "ZUNIVERS",
    "GuildState",
    "ZuniversConfig",
    "ZuniversSection",
    "logger",
    "ninja_config",
    "zunivers_api_url",
    "zunivers_cmd",
    "zunivers_config",
    "zunivers_web_url",
]
