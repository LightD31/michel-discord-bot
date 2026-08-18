"""Shlink REST API client (no Discord imports).

Shlink (https://shlink.io) is a self-hosted URL shortener. The bot uses it for
two things:

- **Automatic shortening** of the external links it posts (RSS entries,
  YouTube uploads). See ``features/links``.
- **Manual management** from the dashboard — the ``/api/shlink/*`` routes wrap
  the CRUD calls below so short URLs can be listed, retargeted, retagged or
  deleted without leaving the Web UI.

Settings live in the global ``shlink`` config section and are read on every
call, so a dashboard edit takes effect without restarting the bot.

The client never raises on the automatic path — callers there use
``features.links.shorten_url`` which swallows :class:`ShlinkError` and falls
back to the original URL. The dashboard routes *do* want the error, so the
methods here raise and let the caller decide.
"""

from __future__ import annotations

import os
import time
from typing import Any
from urllib.parse import quote, urlsplit

from aiohttp import ClientError

from src.core import logging as logutil
from src.core.config import config_store
from src.core.errors import IntegrationError
from src.core.http import http_client

logger = logutil.init_logger(os.path.basename(__file__))

# Shlink's REST API is versioned in the path; v3 is what Shlink >= 3.0 serves.
API_VERSION = "v3"

# How long a ``long URL -> short URL`` mapping stays in the process cache.
# Shlink itself de-duplicates via ``findIfExists``, so the cache only saves
# round trips — a stale entry costs nothing but a redirect through a short URL
# that still resolves.
_CACHE_TTL_SECONDS = 24 * 3600
_CACHE_MAX_ENTRIES = 2000


class ShlinkError(IntegrationError):
    """Raised when a Shlink API call fails."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class ShlinkSettings:
    """Snapshot of the global ``shlink`` config section."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self.enabled: bool = bool(raw.get("enabled", False))
        self.base_url: str = str(raw.get("shlinkBaseUrl") or "").strip().rstrip("/")
        self.api_key: str = str(raw.get("shlinkApiKey") or "").strip()
        self.domain: str = str(raw.get("shlinkDomain") or "").strip()
        self.tags: list[str] = [
            str(t).strip() for t in (raw.get("shlinkTags") or []) if str(t).strip()
        ]
        self.excluded_domains: list[str] = [
            str(d).strip().lower().lstrip("*.")
            for d in (raw.get("shlinkExcludedDomains") or [])
            if str(d).strip()
        ]

    @property
    def configured(self) -> bool:
        """True when the API can be reached at all (URL + key present)."""
        return bool(self.base_url and self.api_key)

    @property
    def short_hosts(self) -> set[str]:
        """Hosts that already belong to this Shlink instance.

        Used to avoid shortening a URL that is already a short URL (which would
        chain redirects and, worse, grow a new short URL on every poll).
        """
        hosts = set()
        for candidate in (self.base_url, f"https://{self.domain}" if self.domain else ""):
            if not candidate:
                continue
            host = urlsplit(candidate).hostname
            if host:
                hosts.add(host.lower())
        return hosts


def get_settings() -> ShlinkSettings:
    """Read the current ``shlink`` section from the reactive config store."""
    config = config_store.get().get("config", {})
    raw = config.get("shlink") or {}
    return ShlinkSettings(raw if isinstance(raw, dict) else {})


class ShlinkClient:
    """Async wrapper around the Shlink REST API.

    Stateless apart from the shortening cache: settings are re-read per call so
    the client follows dashboard edits.
    """

    def __init__(self) -> None:
        # long URL -> (short URL, inserted_at)
        self._cache: dict[str, tuple[str, float]] = {}

    # ── Cache ────────────────────────────────────────────────────────

    def cache_get(self, long_url: str) -> str | None:
        entry = self._cache.get(long_url)
        if entry is None:
            return None
        short_url, inserted_at = entry
        if time.time() - inserted_at > _CACHE_TTL_SECONDS:
            self._cache.pop(long_url, None)
            return None
        return short_url

    def cache_put(self, long_url: str, short_url: str) -> None:
        if len(self._cache) >= _CACHE_MAX_ENTRIES:
            # Cheapest eviction that keeps the dict bounded: drop the oldest
            # insertion (dicts preserve insertion order).
            self._cache.pop(next(iter(self._cache)), None)
        self._cache[long_url] = (short_url, time.time())

    def cache_clear(self) -> None:
        """Drop every memoized mapping (called after a dashboard mutation)."""
        self._cache.clear()

    # ── Transport ────────────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        settings: ShlinkSettings | None = None,
    ) -> Any:
        """Call the Shlink API and return the decoded JSON body (``None`` on 204)."""
        settings = settings or get_settings()
        if not settings.configured:
            raise ShlinkError(
                "Shlink n'est pas configuré — renseignez l'URL de l'instance et la clé API."
            )

        url = f"{settings.base_url}/rest/{API_VERSION}/{path.lstrip('/')}"
        session = await http_client.session()
        try:
            async with session.request(
                method,
                url,
                params=_encode_params(params),
                json=json,
                headers={"X-Api-Key": settings.api_key, "Accept": "application/json"},
            ) as response:
                if response.status == 204:
                    return None
                try:
                    body = await response.json(content_type=None)
                except ValueError:
                    body = None
                if response.status >= 400:
                    raise ShlinkError(_error_detail(body, response.status), status=response.status)
                return body
        except ShlinkError:
            raise
        except (TimeoutError, ClientError) as e:
            raise ShlinkError(f"Shlink injoignable : {e}") from e

    # ── Short URL CRUD ───────────────────────────────────────────────

    async def create_short_url(
        self,
        long_url: str,
        *,
        title: str | None = None,
        tags: list[str] | None = None,
        custom_slug: str | None = None,
        find_if_exists: bool = True,
    ) -> dict[str, Any]:
        """Create (or reuse) a short URL and return the full Shlink object.

        ``find_if_exists`` makes Shlink return the existing short URL when one
        already targets the same long URL with the same tags — that is what
        keeps a feed poller from minting a new slug on every cycle.
        """
        settings = get_settings()
        payload: dict[str, Any] = {"longUrl": long_url, "findIfExists": find_if_exists}
        merged_tags = list(settings.tags)
        for tag in tags or []:
            if tag not in merged_tags:
                merged_tags.append(tag)
        if merged_tags:
            payload["tags"] = merged_tags
        if title:
            payload["title"] = title[:512]
        if custom_slug:
            payload["customSlug"] = custom_slug
        if settings.domain:
            payload["domain"] = settings.domain

        body = await self._request("POST", "short-urls", json=payload, settings=settings)
        if not (body or {}).get("shortUrl"):
            raise ShlinkError("Réponse Shlink sans shortUrl.")
        return body or {}

    async def shorten(
        self,
        long_url: str,
        *,
        title: str | None = None,
        tags: list[str] | None = None,
        use_cache: bool = True,
    ) -> str:
        """Return the short URL for *long_url*, going through the local cache."""
        if use_cache:
            cached = self.cache_get(long_url)
            if cached:
                return cached
        created = await self.create_short_url(long_url, title=title, tags=tags)
        short_url = str(created["shortUrl"])
        self.cache_put(long_url, short_url)
        return short_url

    async def list_short_urls(
        self,
        *,
        page: int = 1,
        items_per_page: int = 25,
        search_term: str | None = None,
        tags: list[str] | None = None,
        order_by: str | None = "dateCreated-DESC",
    ) -> dict[str, Any]:
        """Return one page of short URLs (``{"data": [...], "pagination": {...}}``)."""
        params: dict[str, Any] = {"page": page, "itemsPerPage": items_per_page}
        if search_term:
            params["searchTerm"] = search_term
        if tags:
            params["tags[]"] = tags
        if order_by:
            params["orderBy"] = order_by
        body = await self._request("GET", "short-urls", params=params)
        return (body or {}).get("shortUrls") or {"data": [], "pagination": {}}

    async def get_short_url(self, short_code: str, *, domain: str | None = None) -> dict[str, Any]:
        params = {"domain": domain} if domain else None
        body = await self._request("GET", f"short-urls/{_quote(short_code)}", params=params)
        return body or {}

    async def update_short_url(
        self,
        short_code: str,
        *,
        long_url: str | None = None,
        title: str | None = None,
        tags: list[str] | None = None,
        domain: str | None = None,
    ) -> dict[str, Any]:
        """Patch an existing short URL — this is what "editable" means in the UI."""
        payload: dict[str, Any] = {}
        if long_url is not None:
            payload["longUrl"] = long_url
        if title is not None:
            payload["title"] = title or None
        if tags is not None:
            payload["tags"] = tags
        if not payload:
            raise ShlinkError("Aucune modification demandée.")
        params = {"domain": domain} if domain else None
        body = await self._request(
            "PATCH", f"short-urls/{_quote(short_code)}", params=params, json=payload
        )
        # A retargeted short URL invalidates any memoized mapping.
        self.cache_clear()
        return body or {}

    async def delete_short_url(self, short_code: str, *, domain: str | None = None) -> None:
        params = {"domain": domain} if domain else None
        await self._request("DELETE", f"short-urls/{_quote(short_code)}", params=params)
        self.cache_clear()


def _quote(short_code: str) -> str:
    """Percent-encode a short code so it can only ever be one path segment."""
    return quote(short_code, safe="")


def _encode_params(params: dict[str, Any] | None) -> list[tuple[str, str]]:
    """Flatten query params into aiohttp's ``(key, value)`` pairs.

    aiohttp rejects list values (and anything that isn't a string), while
    Shlink expects repeated ``tags[]`` keys — hence the manual expansion.
    """
    if not params:
        return []
    pairs: list[tuple[str, str]] = []
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            pairs.extend((key, str(item)) for item in value)
        elif isinstance(value, bool):
            pairs.append((key, "true" if value else "false"))
        else:
            pairs.append((key, str(value)))
    return pairs


def _error_detail(body: Any, status: int) -> str:
    """Turn a Shlink RFC-7807 error body into a one-line message."""
    if isinstance(body, dict):
        detail = body.get("detail") or body.get("title")
        if detail:
            return f"Shlink: {detail} (HTTP {status})"
    return f"Shlink: erreur HTTP {status}"


# Process-wide singleton — the cache is only useful if it is shared.
shlink_client = ShlinkClient()


__all__ = [
    "API_VERSION",
    "ShlinkClient",
    "ShlinkError",
    "ShlinkSettings",
    "get_settings",
    "shlink_client",
]
