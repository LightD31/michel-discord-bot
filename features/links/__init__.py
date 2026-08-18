"""Link shortening for the external URLs the bot posts.

Thin async layer over :mod:`src.integrations.shlink` and the pure helpers in
:mod:`features.links.shortener`. Two rules drive the design:

- **Never lose a post.** Every entry point degrades to the original URL when
  Shlink is disabled, unconfigured, or unreachable — a shortener outage must
  not swallow an RSS entry or a go-live notification.
- **Stable output.** Shlink is asked to reuse an existing short URL for a long
  URL it already knows (``findIfExists``), and the client memoizes the mapping,
  so re-rendering the same message yields the same short URL and periodic
  refreshers keep diffing clean.
"""

from __future__ import annotations

import os

from features.links.shortener import (
    clean_url,
    extract_urls,
    is_media_url,
    replace_urls,
    should_shorten,
)
from src.core import logging as logutil
from src.integrations.shlink import ShlinkError, get_settings, shlink_client

logger = logutil.init_logger(os.path.basename(__file__))


def shortening_enabled() -> bool:
    """True when automatic shortening is switched on *and* usable."""
    settings = get_settings()
    return settings.enabled and settings.configured


async def shorten_url(url: str, *, title: str | None = None, tags: list[str] | None = None) -> str:
    """Shorten a single URL, returning it unchanged on any failure or opt-out."""
    if not url:
        return url
    settings = get_settings()
    if not (settings.enabled and settings.configured):
        return url
    if not should_shorten(
        url,
        short_hosts=settings.short_hosts,
        excluded_domains=settings.excluded_domains,
    ):
        return url
    try:
        return await shlink_client.shorten(url, title=title, tags=tags)
    except ShlinkError as e:
        logger.warning("Shlink shortening failed for %s: %s", url, e)
    except Exception as e:  # noqa: BLE001 — a shortener outage must not lose the post
        logger.warning("Unexpected Shlink error for %s: %s", url, e)
    return url


async def shorten_text(text: str, *, tags: list[str] | None = None) -> str:
    """Replace every shortenable URL found in *text* with its short URL.

    Markdown-safe: only the URL span is rewritten, so ``[titre](https://…)``
    keeps its label and trailing punctuation stays outside the link.
    """
    if not text:
        return text
    settings = get_settings()
    if not (settings.enabled and settings.configured):
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
        short = await shorten_url(url, tags=tags)
        if short != url:
            mapping[url] = short
    return replace_urls(text, mapping)


__all__ = [
    "clean_url",
    "extract_urls",
    "is_media_url",
    "replace_urls",
    "shorten_text",
    "shorten_url",
    "shortening_enabled",
    "should_shorten",
]
