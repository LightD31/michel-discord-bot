"""Embed manager: custom embeds of links, edited and published from the dashboard.

Admins build embeds (title, color, links) in the per-guild module config and
publish them into one persistent message of a chosen channel. This extension
only owns the schema; rendering and publishing live in
:mod:`src.discord_ext.embedmanager`, which the dashboard route calls.
"""

from typing import Any

from interactions import Client, Extension

from src.discord_ext.embedmanager import MODULE_KEY
from src.webui.schemas import (
    SchemaBase,
    enabled_field,
    hidden_message_id,
    register_module,
    ui,
)


@register_module(MODULE_KEY)
class EmbedManagerConfig(SchemaBase):
    __label__ = "Gestionnaire d'Embeds"
    __description__ = "Création et publication d'embeds personnalisés."
    __icon__ = "📝"
    __category__ = "Outils"

    enabled: bool = enabled_field()
    channelId: str = ui(
        "Salon de publication",
        "channel",
        required=True,
        description="Salon pour publier les embeds (message créé automatiquement).",
    )
    pinMessage: bool = ui(
        "Épingler le message",
        "boolean",
        default=False,
        description="Épingler automatiquement le message publié.",
    )
    messageId: str | None = hidden_message_id("Message cible", "channelId")
    embeds: list[Any] = ui(
        "Embeds",
        "embedlist",
        description="Créez des embeds avec un titre, couleur et liens.",
        default=[],
    )


class EmbedManagerExtension(Extension):
    """Owns the ``moduleEmbedManager`` schema; the dashboard publishes the embeds.

    Rendering and publishing live in :mod:`src.discord_ext.embedmanager`.
    """

    MODULE_KEYS = (MODULE_KEY,)

    def __init__(self, bot: Client):
        self.bot = bot
