"""Pure URL-scanning helpers behind the Shlink shortening layer.

Everything here is synchronous and dependency-free so it can be unit tested
without a Shlink instance: find the URLs worth shortening in a piece of text,
decide whether a given URL should be shortened at all, and rewrite a text once
the mapping is known.

The "worth shortening" rules exist because a Discord message is not plain text:

- Image/media URLs are fetched by Discord itself to render embed images and
  thumbnails; routing them through a redirect buys nothing and can break the
  preview.
- Discord's own hosts (message links, invites, CDN attachments) are internal
  navigation, not external links.
- URLs already served by our own Shlink instance would otherwise chain a new
  short URL on top of an existing one on every poll.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

# Bare URL matcher. Stops at whitespace and at the delimiters that wrap URLs in
# Discord markdown (``<url>``, ``[label](url)``, quotes) so the closing
# character is never swallowed into the match.
URL_RE = re.compile(r"https?://[^\s<>\"'`\\|)\]}]+", re.IGNORECASE)

# Trailing characters that are almost always sentence punctuation rather than
# part of the URL.
_TRAILING_PUNCTUATION = ".,;:!?…*_~"

# Hosts owned by Discord: linking through a shortener adds a redirect hop to
# navigation that never leaves the client.
_DISCORD_HOSTS = (
    "discord.com",
    "discord.gg",
    "discordapp.com",
    "discordapp.net",
    "discord.media",
)

# Extensions Discord fetches directly to render an embed image/thumbnail/video.
_MEDIA_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".gifv",
    ".webp",
    ".svg",
    ".bmp",
    ".ico",
    ".mp4",
    ".webm",
    ".mov",
    ".mp3",
    ".ogg",
    ".wav",
)


def clean_url(url: str) -> str:
    """Strip trailing punctuation and unbalanced closing parentheses from *url*."""
    cleaned = url.rstrip(_TRAILING_PUNCTUATION)
    # ``(https://example.com/foo)`` — drop the closer only when it isn't part
    # of the path (Wikipedia-style URLs legitimately contain parentheses).
    while cleaned.endswith(")") and cleaned.count(")") > cleaned.count("("):
        cleaned = cleaned[:-1].rstrip(_TRAILING_PUNCTUATION)
    return cleaned


def _host_matches(host: str, domain: str) -> bool:
    """True if *host* is *domain* or one of its subdomains."""
    return host == domain or host.endswith(f".{domain}")


def is_media_url(url: str) -> bool:
    """True when the URL points at an image/audio/video asset."""
    path = urlsplit(url).path.lower()
    return path.endswith(_MEDIA_EXTENSIONS)


def should_shorten(
    url: str,
    *,
    short_hosts: set[str] | None = None,
    excluded_domains: list[str] | None = None,
) -> bool:
    """Decide whether *url* is an external link worth routing through Shlink."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme.lower() not in ("http", "https"):
        return False
    host = (parts.hostname or "").lower()
    if not host:
        return False
    if host in (short_hosts or set()):
        return False
    if any(_host_matches(host, domain) for domain in _DISCORD_HOSTS):
        return False
    if any(_host_matches(host, domain) for domain in (excluded_domains or [])):
        return False
    if is_media_url(url):
        return False
    # A bare domain is already as short as it gets — shortening
    # ``https://twitch.tv`` only makes it less readable.
    return not (parts.path in ("", "/") and not parts.query)


def extract_urls(
    text: str,
    *,
    short_hosts: set[str] | None = None,
    excluded_domains: list[str] | None = None,
) -> list[str]:
    """Return the shortenable URLs in *text*, de-duplicated, in first-seen order."""
    if not text:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for match in URL_RE.finditer(text):
        url = clean_url(match.group(0))
        if not url or url in seen:
            continue
        if not should_shorten(url, short_hosts=short_hosts, excluded_domains=excluded_domains):
            continue
        seen.add(url)
        found.append(url)
    return found


def replace_urls(text: str, mapping: dict[str, str]) -> str:
    """Rewrite every URL of *text* that appears in *mapping*.

    Punctuation trimmed by :func:`clean_url` is preserved: only the URL part of
    the match is swapped, so ``(https://example.com/x)`` keeps its parentheses.
    """
    if not text or not mapping:
        return text

    def _swap(match: re.Match[str]) -> str:
        raw = match.group(0)
        url = clean_url(raw)
        replacement = mapping.get(url)
        if not replacement:
            return raw
        return replacement + raw[len(url) :]

    return URL_RE.sub(_swap, text)


__all__ = [
    "URL_RE",
    "clean_url",
    "extract_urls",
    "is_media_url",
    "replace_urls",
    "should_shorten",
]
