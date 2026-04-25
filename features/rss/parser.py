"""RSS / Atom parser built on :mod:`defusedxml`.

Pulls in zero compiled dependencies (avoids ``feedparser``'s ``sgmllib3k``
build failure on modern setuptools) and supports the two formats we actually
care about: RSS 2.0 (``<rss><channel>``) and Atom 1.0 (``<feed>``).

Pure parsing only — the async HTTP fetch lives in
:mod:`features.rss.network` so this module stays importable without the
``aiohttp``/``src.core`` chain (handy for tests).
"""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from xml.etree.ElementTree import Element

from defusedxml import ElementTree as ET

from features.rss.models import RssEntry
from src.core.errors import IntegrationError

_TAG_RE = re.compile(r"<[^>]+>")
_ATOM_NS = "{http://www.w3.org/2005/Atom}"


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


def _localname(tag: str) -> str:
    """Return the local name of an XML tag, dropping any ``{namespace}`` prefix."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _text(elem: Element | None) -> str:
    """Return all text inside *elem*, including descendants and tails.

    Feeds occasionally drop literal HTML inside an unescaped ``<title>`` /
    ``<description>``. defusedxml parses those tags as XML children, so naïve
    ``elem.text`` would only return the prefix before the first child. We
    walk the subtree and re-join everything, then ``strip_html`` upstream
    normalizes the result.
    """
    if elem is None:
        return ""
    parts: list[str] = []
    if elem.text:
        parts.append(elem.text)
    for child in elem:
        parts.append(_text(child))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts).strip()


def _find_local(parent: Element, name: str) -> Element | None:
    """Find a direct child by local name regardless of namespace."""
    for child in parent:
        if _localname(child.tag) == name:
            return child
    return None


def _findall_local(parent: Element, name: str) -> list[Element]:
    return [child for child in parent if _localname(child.tag) == name]


def _parse_rfc822(value: str) -> datetime | None:
    """Parse an RFC 822 / RSS ``pubDate``. Returns UTC-aware ``datetime`` or ``None``."""
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _parse_iso8601(value: str) -> datetime | None:
    """Parse an ISO 8601 / Atom ``updated``. Returns UTC-aware ``datetime`` or ``None``."""
    if not value:
        return None
    # Python <3.11 didn't accept the trailing ``Z``; the codebase targets 3.12+
    # so this is just defensive.
    cleaned = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _atom_link(entry: Element) -> str:
    """Return the best ``<link>`` URL for an Atom entry.

    Prefers ``rel="alternate"`` (or no rel — that's the default) over
    ``rel="self"``/``rel="enclosure"``.
    """
    fallback = ""
    for link in _findall_local(entry, "link"):
        href = link.get("href", "")
        if not href:
            continue
        rel = link.get("rel", "alternate")
        if rel == "alternate":
            return href
        if not fallback:
            fallback = href
    return fallback


def _atom_author(entry: Element) -> str:
    author = _find_local(entry, "author")
    if author is None:
        return ""
    name = _find_local(author, "name")
    return _text(name) or _text(author)


def _parse_atom_entry(entry: Element) -> RssEntry | None:
    eid = _text(_find_local(entry, "id"))
    title = _text(_find_local(entry, "title"))
    link = _atom_link(entry)
    if not eid:
        eid = link or title
    if not eid:
        return None
    summary = _text(_find_local(entry, "summary")) or _text(_find_local(entry, "content"))
    published_text = _text(_find_local(entry, "published")) or _text(_find_local(entry, "updated"))
    return RssEntry(
        entry_id=eid,
        title=strip_html(title) or "(sans titre)",
        link=link,
        summary=strip_html(summary),
        author=_atom_author(entry),
        published=_parse_iso8601(published_text),
    )


def _parse_rss_item(item: Element) -> RssEntry | None:
    title = _text(_find_local(item, "title"))
    link = _text(_find_local(item, "link"))
    guid = _text(_find_local(item, "guid"))
    eid = guid or link or title
    if not eid:
        return None
    summary = _text(_find_local(item, "description"))
    published_text = _text(_find_local(item, "pubDate"))
    author = _text(_find_local(item, "author")) or _text(_find_local(item, "creator"))
    return RssEntry(
        entry_id=eid,
        title=strip_html(title) or "(sans titre)",
        link=link,
        summary=strip_html(summary),
        author=author,
        published=_parse_rfc822(published_text),
    )


def parse_feed(body: str) -> list[RssEntry]:
    """Parse an RSS 2.0 or Atom 1.0 feed into a list of :class:`RssEntry`.

    Returns the entries in the order the feed exposes them — typically newest
    first. Raises :class:`src.core.errors.IntegrationError` if the body is
    neither valid XML nor a recognized feed shape.
    """
    if not body or not body.strip():
        raise IntegrationError("Empty feed body")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise IntegrationError(f"Could not parse feed: {e}") from e

    out: list[RssEntry] = []
    local = _localname(root.tag)
    if local == "feed":
        # Atom 1.0
        for entry in _findall_local(root, "entry"):
            item = _parse_atom_entry(entry)
            if item is not None:
                out.append(item)
        return out
    if local == "rss":
        channel = _find_local(root, "channel")
        if channel is None:
            return out
        for item in _findall_local(channel, "item"):
            parsed = _parse_rss_item(item)
            if parsed is not None:
                out.append(parsed)
        return out
    if local == "RDF":
        # RSS 1.0 — ``<item>`` elements are siblings of ``<channel>``.
        for item in _findall_local(root, "item"):
            parsed = _parse_rss_item(item)
            if parsed is not None:
                out.append(parsed)
        return out
    raise IntegrationError(f"Unrecognized feed root element: {local!r}")


def normalize_entry(entry: Element) -> RssEntry | None:
    """Public for symmetry with the previous feedparser-based API.

    Dispatches based on the entry's tag (Atom ``entry`` vs. RSS ``item``).
    """
    local = _localname(entry.tag)
    if local == "entry":
        return _parse_atom_entry(entry)
    if local == "item":
        return _parse_rss_item(entry)
    return None


__all__ = ["normalize_entry", "parse_feed", "strip_html"]
