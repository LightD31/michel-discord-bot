"""Render and publish the Embed manager's custom embeds.

The embeds are configured per guild in ``moduleEmbedManager`` and published
from the dashboard (``src/webui/routes/servers.py``) into one persistent
message. Lives in ``src/discord_ext`` because it builds interactions.py
objects; the extension (``extensions/embedmanager.py``) only owns the schema.
"""

from __future__ import annotations

import logging
from typing import Any

from interactions import Client, Embed

from features.links import shorten_keyed
from src.core import logging as logutil
from src.discord_ext.messages import edit_message_if_changed, fetch_or_create_persistent_message

MODULE_KEY = "moduleEmbedManager"
DEFAULT_COLOR = "3498db"

logger = logutil.init_logger(__name__)


def build_embeds(embeds_config: list[Any]) -> list[Embed]:
    """Build one embed per entry of *embeds_config*.

    Each entry is a dict with a ``title``, a hex ``color`` (``#`` optional)
    and ``links``, a list of ``{title, url}`` rendered as one field each.
    Entries that aren't dicts are skipped, an invalid color falls back to the
    default, and a link without a URL is left out.
    """
    embeds = []
    for embed_data in embeds_config:
        if not isinstance(embed_data, dict):
            logger.warning("Invalid embed config: %r", embed_data)
            continue

        color_hex = str(embed_data.get("color") or DEFAULT_COLOR).lstrip("#")
        try:
            color = int(color_hex, 16)
        except ValueError:
            logger.warning("Invalid color format: %s, using default", color_hex)
            color = int(DEFAULT_COLOR, 16)
        embed = Embed(title=embed_data.get("title", "Sans titre"), color=color)

        links = embed_data.get("links")
        for link_data in links if isinstance(links, list) else []:
            if not isinstance(link_data, dict) or not link_data.get("url"):
                continue
            embed.add_field(
                name=link_data.get("title", "Lien"),
                value=f"[Cliquez ici]({link_data['url']})",
                inline=False,
            )
        embeds.append(embed)
    return embeds


async def shorten_links_config(embeds_config: list[Any], guild_id: str) -> list[Any]:
    """Return *embeds_config* with every link URL replaced by its short URL.

    Each link gets its own short URL *slot*, keyed by guild + embed title +
    link title, so editing a link's target in the dashboard retargets the
    existing short URL instead of minting a new one — short links already
    shared in Discord keep working. Renaming the embed or the link is a new
    slot (and therefore a new short URL), since the slug follows the name.
    """
    shortened: list[Any] = []
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


async def publish_embeds(
    bot: Client,
    guild_id: str,
    guild_config: dict[str, Any],
    *,
    log: logging.Logger | None = None,
) -> bool:
    """Show the guild's configured embeds in its persistent message (on the bot loop).

    The links go through Shlink first, which needs the bot loop's HTTP
    session. The message is created (and its id saved) when missing. Returns
    False when the message can't be fetched or created.
    """
    embeds = build_embeds(await shorten_links_config(guild_config.get("embeds") or [], guild_id))
    message = await fetch_or_create_persistent_message(
        bot,
        guild_id=guild_id,
        slot="embedmanager",
        channel_id=guild_config.get("channelId"),
        label="Embeds",
        initial_content="Initialisation…",
        pin=bool(guild_config.get("pinMessage", False)),
        logger=log or logger,
    )
    if message is None:
        return False
    await edit_message_if_changed(message, content="", embeds=embeds, logger=log or logger)
    return True


__all__ = ["MODULE_KEY", "build_embeds", "publish_embeds", "shorten_links_config"]
