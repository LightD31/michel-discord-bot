"""Async HTTP fetch for RSS feeds — kept separate from the parser.

Splitting this out lets ``features.rss.parser`` stay a pure module (import-able
without ``aiohttp``/``src.core``) while extensions still get a single helper
to call.
"""

from __future__ import annotations

from features.rss.models import RssEntry
from features.rss.parser import parse_feed
from src.core.http import fetch_untrusted


async def fetch_feed(url: str) -> list[RssEntry]:
    """Download *url* and parse it. Raises :class:`src.core.errors.IntegrationError` on failure.

    Feed URLs come from guild admins (dashboard) and ``/rss test``, so they go
    through :func:`~src.core.http.fetch_untrusted`: public addresses only, body
    size capped — never the bot's own network.
    """
    body = await fetch_untrusted(url, return_type="text")
    return parse_feed(body)


__all__ = ["fetch_feed"]
