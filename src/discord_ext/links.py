"""Embed-level link shortening.

``features.links`` knows which URLs are worth shortening; this module applies
it to the parts of a Discord ``Embed`` that carry *navigational* links — the
title URL, the description, field values and the author URL — and leaves the
parts Discord fetches itself (image, thumbnail, footer/author icons) untouched.

Safe on periodically refreshed embeds: ``features.links.shorten_url`` reuses
the same short URL for a given long URL, and falls back to the last one it
minted when Shlink is unreachable, so a refresh renders a byte-identical
payload and ``edit_message_if_changed`` still skips the edit.
"""

from __future__ import annotations

from collections.abc import Iterable

from interactions import Embed

from features.links import shorten_text, shorten_url, shortening_enabled

__all__ = ["shorten_embed", "shorten_embeds"]


async def shorten_embed(embed: Embed, *, tags: list[str] | None = None) -> Embed:
    """Shorten the links carried by *embed*, in place, and return it."""
    if embed is None or not shortening_enabled():
        return embed

    if embed.url:
        embed.url = await shorten_url(embed.url, title=embed.title, tags=tags)
    if embed.description:
        embed.description = await shorten_text(embed.description, tags=tags)
    for field in embed.fields or []:
        if field.value:
            field.value = await shorten_text(field.value, tags=tags)
    author = embed.author
    if author is not None and author.url:
        author.url = await shorten_url(author.url, title=author.name, tags=tags)
    return embed


async def shorten_embeds(embeds: Iterable[Embed], *, tags: list[str] | None = None) -> list[Embed]:
    """Apply :func:`shorten_embed` to every embed of an iterable."""
    result = []
    for embed in embeds:
        result.append(await shorten_embed(embed, tags=tags))
    return result
