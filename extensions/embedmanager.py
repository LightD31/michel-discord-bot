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

from features.links import shorten_keyed
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


async def shorten_links_config(embeds_config: list[dict], guild_id: str) -> list[dict]:
    """Return *embeds_config* with every link URL replaced by its short URL.

    Each link gets its own short URL *slot*, keyed by guild + embed title +
    link title, so editing a link's target in the dashboard retargets the
    existing short URL instead of minting a new one — short links already
    shared in Discord keep working. Renaming the embed or the link is a new
    slot (and therefore a new short URL), since the slug follows the name.
    """
    shortened: list[dict] = []
    for index, embed_data in enumerate(embeds_config, start=1):
        if not isinstance(embed_data, dict):
            shortened.append(embed_data)
            continue
        embed_copy = dict(embed_data)
        embed_title = str(embed_copy.get("title") or f"embed-{index}")
        links = embed_copy.get("links")
        if isinstance(links, list):
            new_links = []
            for link_index, link_data in enumerate(links, start=1):
                if not isinstance(link_data, dict) or not link_data.get("url"):
                    new_links.append(link_data)
                    continue
                link_copy = dict(link_data)
                link_title = str(link_copy.get("title") or f"lien-{link_index}")
                link_copy["url"] = await shorten_keyed(
                    f"{guild_id}-{embed_title}-{link_title}",
                    str(link_copy["url"]),
                    title=f"{embed_title} — {link_title}",
                    tags=["embedmanager"],
                )
                new_links.append(link_copy)
            embed_copy["links"] = new_links
        shortened.append(embed_copy)
    return shortened


async def build_embeds_with_short_links(embeds_config: list[dict], guild_id: str) -> list[Embed]:
    """Build the embeds with every configured link routed through Shlink."""
    return build_embeds(await shorten_links_config(embeds_config, guild_id))


class EmbedManagerExtension(Extension):
    """EmbedManager extension for publishing custom embeds with links."""

    def __init__(self, bot: Client):
        self.bot = bot
