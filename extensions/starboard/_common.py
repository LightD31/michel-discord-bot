"""Config schema, logger, and shared helpers for the starboard extension."""

import os

from src.core import logging as logutil
from src.core.config import ModuleConfig
from src.webui.schemas import SchemaBase, enabled_field, register_module, ui

logger = logutil.init_logger(__name__)


@register_module("moduleStarboard")
class StarboardConfig(SchemaBase):
    __label__ = "Starboard"
    __description__ = "Mise en avant des messages populaires dans un salon dédié."
    __icon__ = "⭐"
    __category__ = "Communauté"

    enabled: bool = enabled_field()
    starboardChannelId: str = ui(
        "Salon starboard",
        "channel",
        required=True,
        description="Salon où les messages mis en avant sont republiés.",
    )
    emoji: str = ui(
        "Emoji déclencheur",
        "string",
        default="⭐",
        description="Emoji utilisé pour valider un message (Unicode ou nom :emoji:).",
    )
    threshold: int = ui(
        "Seuil",
        "number",
        default=3,
        description="Nombre minimum de réactions pour publier le message.",
    )
    allowSelfStar: bool = ui(
        "Auto-starring",
        "boolean",
        default=False,
        description="Autoriser un membre à mettre en avant son propre message.",
    )
    ignoreBots: bool = ui(
        "Ignorer les bots",
        "boolean",
        default=True,
        description="Ne pas mettre en avant les messages envoyés par des bots.",
    )
    removeBelowThreshold: bool = ui(
        "Retirer si seuil retombé",
        "boolean",
        default=False,
        description="Supprimer le message du starboard si le compteur passe sous le seuil.",
    )
    ignoredChannels: list[str] = ui(
        "Salons ignorés",
        "list",
        description="IDs de salons à exclure du starboard.",
    )


STARBOARD = ModuleConfig("moduleStarboard")


def get_guild_settings(guild_id: int | str) -> dict | None:
    """Return per-guild module config dict, or None if disabled/missing."""
    return STARBOARD.get(guild_id) or None
