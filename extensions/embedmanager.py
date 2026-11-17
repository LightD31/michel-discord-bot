"""Embed manager: custom embeds of links, edited and published from the dashboard.

Admins build embeds (title, color, links) in the per-guild module config and
publish them into one persistent message of a chosen channel. This extension
only owns the schema; rendering and publishing live in
:mod:`src.discord_ext.embedmanager`, which the dashboard route calls.
"""

from interactions import Client, Extension
from pydantic import BaseModel, ConfigDict, Field

from src.discord_ext.embedmanager import MODULE_KEY
from src.webui.schemas import (
    SchemaBase,
    enabled_field,
    register_module,
    ui,
)


class EmbedLink(BaseModel):
    """A link of an embed: a field title and the URL it points to."""

    model_config = ConfigDict(extra="forbid")

    title: str
    url: str


class EmbedEntry(BaseModel):
    """One entry of the ``embedlist`` widget, as the SPA writes it.

    The color is written back into a color input: six hex digits, with or
    without the ``#`` the SPA strips.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = "Embed"
    color: str = Field(default="3498db", pattern=r"^#?[0-9A-Fa-f]{6}$")
    links: list[EmbedLink] = Field(default_factory=list)


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
    embeds: list[EmbedEntry] = ui(
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
