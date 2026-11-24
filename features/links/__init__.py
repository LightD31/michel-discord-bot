"""Link shortening for the external URLs the bot posts.

Thin async layer over :mod:`src.integrations.shlink` and the pure helpers in
:mod:`features.links.shortener`. Two rules drive the design:

- **Never lose a post.** Every entry point degrades to the original URL when
  Shlink is disabled, unconfigured, or unreachable — a shortener outage must
  not swallow an RSS entry or a go-live notification.
- **The server's own instance** (P37). Every entry point takes the
  ``guild_id`` the link is posted in; ``None`` (a DM) posts it as it is.
- **Stable output.** Shlink is asked to reuse an existing short URL for a long
  URL it already knows (``findIfExists``), and the client memoizes the mapping,
  so re-rendering the same message yields the same short URL and periodic
  refreshers keep diffing clean.
"""

from __future__ import annotations

from features.links.shortener import (
    clean_url,
    extract_urls,
    is_media_url,
    replace_urls,
    should_shorten,
    slot_slug,
)
from src.core import logging as logutil
from src.integrations.shlink import ShlinkError, ShlinkSettings, get_settings, shlink_for

logger = logutil.init_logger(__name__)


GuildId = str | int | None


def _active(guild_id: GuildId) -> ShlinkSettings | None:
    """The server's settings when it shortens automatically, else ``None``."""
    if guild_id is None:
        return None
    settings = get_settings(guild_id)
    return settings if settings.enabled and settings.configured else None


def shortening_enabled(guild_id: GuildId) -> bool:
    """True when the server's automatic shortening is switched on *and* usable."""
    return _active(guild_id) is not None


async def shorten_url(
    url: str, *, guild_id: GuildId, title: str | None = None, tags: list[str] | None = None
) -> str:
    """Shorten a single URL, returning it unchanged on any failure or opt-out."""
    if not url:
        return url
    settings = _active(guild_id)
    if settings is None:
        return url
    client = shlink_for(guild_id)
    if not should_shorten(
        url,
        short_hosts=settings.short_hosts,
        excluded_domains=settings.excluded_domains,
    ):
        return url
    try:
        return await client.shorten(url, title=title, tags=tags)
    except ShlinkError as e:
        logger.warning("Shlink shortening failed for %s: %s", url, e)
    except Exception as e:  # noqa: BLE001 — a shortener outage must not lose the post
        logger.warning("Unexpected Shlink error for %s: %s", url, e)
    # Fall back to the last short URL we minted for this link, even an expired
    # one: a periodically refreshed embed must keep rendering the same URL
    # through a Shlink outage instead of flipping back to the long one and
    # churning an edit on every cycle.
    return client.cache_get(url, allow_stale=True) or url


async def shorten_keyed(
    key: str,
    url: str,
    *,
    guild_id: GuildId,
    title: str | None = None,
    tags: list[str] | None = None,
) -> str:
    """Return a *stable* short URL for the link slot named by *key*.

    Unlike :func:`shorten_url`, the short URL is tied to the slot rather than
    to the target: the slug comes from *key*, so when the configured URL
    changes the existing short link is retargeted (PATCH) instead of a second
    one being created. Links already posted in Discord keep resolving, now to
    the new destination.

    Use it for links that have an identity — a guild's Spotify dashboard, an
    Embed-manager entry — and :func:`shorten_url` for one-off targets such as
    an RSS entry, where every URL is its own thing.
    """
    if not url:
        return url
    settings = _active(guild_id)
    if settings is None:
        return url
    client = shlink_for(guild_id)
    if not should_shorten(
        url,
        short_hosts=settings.short_hosts,
        excluded_domains=settings.excluded_domains,
    ):
        return url
    try:
        slug = slot_slug(key)
    except ValueError as e:
        logger.warning("%s — lien non raccourci", e)
        return url
    try:
        return await client.retarget_or_create(slug, url, title=title, tags=tags)
    except ShlinkError as e:
        logger.warning("Shlink slot %s failed for %s: %s", slug, url, e)
    except Exception as e:  # noqa: BLE001 — a shortener outage must not lose the post
        logger.warning("Unexpected Shlink error for slot %s: %s", slug, e)
    # Same reasoning as shorten_url: prefer the slot's last known short URL so
    # a refreshed message keeps rendering the same payload.
    return client.slot_get(slug) or client.cache_get(url, allow_stale=True) or url


async def shorten_text(text: str, *, guild_id: GuildId, tags: list[str] | None = None) -> str:
    """Replace every shortenable URL found in *text* with its short URL.

    Markdown-safe: only the URL span is rewritten, so ``[titre](https://…)``
    keeps its label and trailing punctuation stays outside the link.
    """
    if not text:
        return text
    settings = _active(guild_id)
    if settings is None:
        return text
    urls = extract_urls(
        text,
        short_hosts=settings.short_hosts,
        excluded_domains=settings.excluded_domains,
    )
    if not urls:
        return text
    mapping: dict[str, str] = {}
    for url in urls:
        short = await shorten_url(url, guild_id=guild_id, tags=tags)
        if short != url:
            mapping[url] = short
    return replace_urls(text, mapping)


__all__ = [
    "clean_url",
    "extract_urls",
    "is_media_url",
    "replace_urls",
    "shorten_keyed",
    "shorten_text",
    "shorten_url",
    "shortening_enabled",
    "should_shorten",
    "slot_slug",
]
