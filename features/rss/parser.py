"""RSS / Atom parser.

Wraps :mod:`feedparser` with a small normalization layer so callers receive a
list of :class:`features.rss.models.RssEntry` regardless of the feed flavor
(RSS 2.0, Atom 1.0, RDF). Pure parsing only — the async HTTP fetch lives in
:mod:`features.rss.network` so this module stays importable without the
``aiohttp``/``src.core`` chain (handy for tests).
"""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from typing import Any

import feedparser

from features.rss.models import RssEntry
from src.core.errors import IntegrationError

_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text: str | None, *, max_length: int = 400) -> str:
    """Strip tags + decode entities + collapse whitespace, truncate to ``max_length``."""
    if not text:
        return ""
    cleaned = _TAG_RE.sub(" ", text)
    cleaned = html.unescape(cleaned)
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > max_length:
        cleaned = cleaned[: max_length - 1].rstrip() + "…"
    return cleaned


def _entry_id(entry: dict[str, Any]) -> str:
    """Pick the most stable identifier feedparser exposes for an entry."""
    for key in ("id", "guid", "link"):
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value
    return entry.get("title", "") or ""


def _entry_published(entry: dict[str, Any]) -> datetime | None:
    """Return a ``datetime`` for the entry, preferring ``published`` over ``updated``."""
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            try:
                return datetime(*parsed[:6], tzinfo=UTC)
            except (TypeError, ValueError):
                continue
    return None


def normalize_entry(entry: dict[str, Any]) -> RssEntry | None:
    """Convert a feedparser entry dict into an :class:`RssEntry`. ``None`` if unusable."""
    eid = _entry_id(entry)
    if not eid:
        return None
    title = strip_html(entry.get("title", "")) or "(sans titre)"
    link = entry.get("link", "") or ""
    summary = strip_html(entry.get("summary") or entry.get("description") or "")
    author = entry.get("author") or ""
    return RssEntry(
        entry_id=eid,
        title=title,
        link=link,
        summary=summary,
        author=str(author),
        published=_entry_published(entry),
    )


def parse_feed(body: str) -> list[RssEntry]:
    """Parse a feed body into a list of :class:`RssEntry` (most recent first).

    Returns the entries in the order the feed exposes them — typically newest
    first, but feeds are inconsistent so callers should not rely on it for
    correctness, only for display ordering.
    """
    parsed = feedparser.parse(body)
    if parsed.bozo and not parsed.entries:
        # ``bozo`` flags any parse warning; only fail when it produced nothing usable.
        reason = getattr(parsed, "bozo_exception", None)
        raise IntegrationError(f"Could not parse feed: {reason}")
    out: list[RssEntry] = []
    for raw in parsed.entries:
        item = normalize_entry(raw)
        if item is not None:
            out.append(item)
    return out


__all__ = ["normalize_entry", "parse_feed", "strip_html"]
