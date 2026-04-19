"""
EmbedManager Extension - Custom Embed Publisher

This extension allows admins to create and manage embeds with arbitrary
links through the web UI. Embeds are configured in the per-guild module config
and published to a designated channel message.

Features:
- Create embeds with custom titles and colors
- Add multiple links with titles to each embed
- Automatically publish embeds to a configured message
- Edit embeds through the web UI
"""

import os
from typing import Any

from interactions import (
    Client,
    Embed,
    Extension,
)

from src.core import logging as logutil
from src.webui.schemas import (
    SchemaBase,
    enabled_field,
    hidden_message_id,
    register_module,
    ui,
)


@register_module("moduleEmbedManager")
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


logger = logutil.init_logger(os.path.basename(__file__))


def build_embeds(embeds_config: list[dict]) -> list[Embed]:
    """Build Discord embeds from configuration.

    Args:
        embeds_config: List of embed configurations, each with:
            - title: str (embed title)
            - color: str (hex color code without #)
            - links: list[dict] (optional, list of {title, url})

    Returns:
        List of Embed objects
    """
    embeds = []

    for embed_data in embeds_config:
        if not isinstance(embed_data, dict):
            logger.warning(f"Invalid embed config: {embed_data}")
            continue

        title = embed_data.get("title", "Sans titre")
        color_hex = embed_data.get("color", "3498db").lstrip("#")

        try:
            color_int = int(color_hex, 16)
        except ValueError:
            logger.warning(f"Invalid color format: {color_hex}, using default")
            color_int = int("3498db", 16)

        embed = Embed(title=title, color=color_int)

        links = embed_data.get("links", [])
        if links and isinstance(links, list):
            for link_data in links:
                if not isinstance(link_data, dict):
                    continue
                link_title = link_data.get("title", "Lien")
                link_url = link_data.get("url", "")

                if link_url:
                    embed.add_field(
                        name=link_title,
                        value=f"[Cliquez ici]({link_url})",
                        inline=False,
                    )

        embeds.append(embed)

    return embeds


class EmbedManagerExtension(Extension):
    """EmbedManager extension for publishing custom embeds with links."""

    def __init__(self, bot: Client):
        self.bot = bot
