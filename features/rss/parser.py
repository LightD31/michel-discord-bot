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
_IMG_SRC_RE = re.compile(r"""<img[^>]*\bsrc=["']([^"']+)["']""", re.IGNORECASE)
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_HR_RE = re.compile(r"<hr\s*/?>", re.IGNORECASE)
_BLOCK_END_RE = re.compile(
    r"</(p|div|li|ul|ol|h[1-6]|tr|blockquote|section|article|header|footer)\s*>",
    re.IGNORECASE,
)
# Drop fine-print blocks (``<small>``) entirely — they're typically source /
# generator credits that just add noise in a Discord embed.
_SMALL_RE = re.compile(r"<small[^>]*>.*?</small>", re.IGNORECASE | re.DOTALL)
_BOLD_RE = re.compile(r"</?(b|strong)\s*>", re.IGNORECASE)
_LINK_RE = re.compile(
    r"""<a\s[^>]*?href=["']([^"']+)["'][^>]*>(.*?)</a>""",
    re.IGNORECASE | re.DOTALL,
)
_PUNCT_LEFT_RE = re.compile(r" +([.,!?:;)\]])")
_PUNCT_RIGHT_RE = re.compile(r"([(\[]) +")
_EMPTY_BOLD_RE = re.compile(r"\*\*\s*\*\*")


def _extract_image_from_html(html_text: str | None) -> str:
    """Return the ``src`` of the first ``<img>`` tag in *html_text*. ``""`` if none."""
    if not html_text:
        return ""
    match = _IMG_SRC_RE.search(html_text)
    return match.group(1) if match else ""


def _link_to_markdown(match: re.Match[str]) -> str:
    href = match.group(1).strip()
    inner = _TAG_RE.sub("", match.group(2))
    inner = " ".join(inner.split())
    return f"[{inner}]({href})" if inner else href


def strip_html(text: str | None, *, max_length: int = 3900) -> str:
    """Render simple HTML into Discord-friendly markdown.

    Translates the few constructs that map cleanly to Discord's markdown
    flavor so feeds like LootScraper read like real messages rather than
    blobs of text:

    * ``<b>`` / ``<strong>`` become ``**bold**``
    * ``<a href="X">Y</a>`` becomes ``[Y](X)``
    * ``<br>``, ``<hr>``, and block-level closers (``</p>``, ``</li>``, …)
      become real newlines
    * ``<small>…</small>`` is dropped entirely (source/generator credits)
    * Remaining tags are stripped (replaced with a space to avoid welding
      adjacent words together)

    Then collapses horizontal whitespace per line, fixes the ``( X )`` /
    ``Y .`` artifacts left behind by inline-tag stripping, collapses runs
    of blank lines, and truncates to ``max_length``.
    """
    if not text:
        return ""
    cleaned = _SMALL_RE.sub("", text)
    cleaned = _LINK_RE.sub(_link_to_markdown, cleaned)
    cleaned = _BR_RE.sub("\n", cleaned)
    cleaned = _HR_RE.sub("\n", cleaned)
    cleaned = _BLOCK_END_RE.sub("\n", cleaned)
    cleaned = _BOLD_RE.sub("**", cleaned)
    cleaned = _TAG_RE.sub(" ", cleaned)
    cleaned = html.unescape(cleaned)
    out_lines: list[str] = []
    blank = False
    for raw_line in cleaned.split("\n"):
        line = " ".join(raw_line.split())
        line = _PUNCT_LEFT_RE.sub(r"\1", line)
        line = _PUNCT_RIGHT_RE.sub(r"\1", line)
        line = _EMPTY_BOLD_RE.sub("", line).strip()
        if line:
            out_lines.append(line)
            blank = False
        elif out_lines and not blank:
            out_lines.append("")
            blank = True
    cleaned = "\n".join(out_lines).strip()
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


def _extract_image(entry: Element, summary_html: str = "") -> str:
    """Return the best image URL for *entry*.

    Walks namespaced media elements (``media:thumbnail``, ``media:content``)
    and ``enclosure`` tags, then inline XHTML ``<img>`` children (Atom
    ``<content type="xhtml">`` parses image tags as real XML elements rather
    than escaped HTML text — that's how LootScraper exposes cover art).
    Falls back to scanning the rendered summary/content HTML for an
    ``<img src>``.
    """
    img_fallback = ""
    for child in entry.iter():
        local = _localname(child.tag)
        if local == "thumbnail":
            url = child.get("url") or child.text
            if url:
                return url.strip()
        if local == "content":
            medium = (child.get("medium") or "").lower()
            ctype = (child.get("type") or "").lower()
            if medium == "image" or ctype.startswith("image/"):
                url = child.get("url")
                if url:
                    return url.strip()
        if local == "enclosure":
            ctype = (child.get("type") or "").lower()
            if ctype.startswith("image/"):
                url = child.get("url") or child.get("href")
                if url:
                    return url.strip()
        if local == "img" and not img_fallback:
            src = child.get("src")
            if src:
                img_fallback = src.strip()
    if img_fallback:
        return img_fallback
    return _extract_image_from_html(summary_html)


def _parse_atom_entry(entry: Element) -> RssEntry | None:
    eid = _text(_find_local(entry, "id"))
    title = _text(_find_local(entry, "title"))
    link = _atom_link(entry)
    if not eid:
        eid = link or title
    if not eid:
        return None
    raw_summary = _text(_find_local(entry, "summary")) or _text(_find_local(entry, "content"))
    published_text = _text(_find_local(entry, "published")) or _text(_find_local(entry, "updated"))
    return RssEntry(
        entry_id=eid,
        title=strip_html(title) or "(sans titre)",
        link=link,
        summary=strip_html(raw_summary),
        author=_atom_author(entry),
        published=_parse_iso8601(published_text),
        image_url=_extract_image(entry, raw_summary),
    )


def _parse_rss_item(item: Element) -> RssEntry | None:
    title = _text(_find_local(item, "title"))
    link = _text(_find_local(item, "link"))
    guid = _text(_find_local(item, "guid"))
    eid = guid or link or title
    if not eid:
        return None
    raw_summary = _text(_find_local(item, "description"))
    published_text = _text(_find_local(item, "pubDate"))
    author = _text(_find_local(item, "author")) or _text(_find_local(item, "creator"))
    return RssEntry(
        entry_id=eid,
        title=strip_html(title) or "(sans titre)",
        link=link,
        summary=strip_html(raw_summary),
        author=author,
        published=_parse_rfc822(published_text),
        image_url=_extract_image(item, raw_summary),
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
