"""Shared HTTP client for extensions.

A single long-lived ``aiohttp`` session is reused across the whole process,
which:

- Reuses TCP connections (DNS + TLS handshake amortized).
- Has a single place to set defaults (timeouts, headers, user-agent).
- Counts every request per host and outcome in :mod:`src.core.health`
  (``michel_http_requests_total`` once the Prometheus exporter is on).

Usage::

    from src.core.http import fetch

    body = await fetch("https://example.com/api", return_type="json")
"""

from __future__ import annotations

import asyncio
import json
import random
import socket
import threading
from typing import Any, ClassVar
from urllib.parse import urljoin, urlsplit

from aiohttp import (
    ClientConnectorError,
    ClientError,
    ClientResponse,
    ClientSession,
    ClientTimeout,
    TCPConnector,
    TraceConfig,
    TraceRequestEndParams,
    TraceRequestExceptionParams,
)
from aiohttp.abc import AbstractResolver, ResolveResult
from aiohttp.resolver import DefaultResolver

from src.core import logging as _logging
from src.core.errors import HttpError
from src.core.health import health
from src.core.urls import host_is_ip_literal, is_public_address, redact_url

logger = _logging.init_logger(__name__)

# Default User-Agent kept identical to the previous ``src/utils.fetch`` impl
# so servers that gate on UA keep accepting our requests.
_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
)
_DEFAULT_TIMEOUT = ClientTimeout(total=30)

# Limits for fetch_untrusted(): URLs chosen by guild admins or members.
UNTRUSTED_TIMEOUT = ClientTimeout(total=20)
UNTRUSTED_MAX_BYTES = 5 * 1024 * 1024
UNTRUSTED_MAX_REDIRECTS = 5
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class BlockedUrlError(HttpError):
    """The URL points somewhere the bot must not go on someone else's behalf.

    Raised by :func:`fetch_untrusted` for a non-http(s) scheme or a host that is
    (or resolves to) a non-public address: loopback, the container network,
    cloud metadata endpoints…
    """


class _BlockedAddress(OSError):
    """Raised inside the resolver; :func:`fetch_untrusted` turns it into BlockedUrlError."""


class _PublicOnlyResolver(AbstractResolver):
    """DNS resolver that drops every non-public address (see :func:`is_public_address`).

    The connector connects only to what the resolver returns, so checking here —
    not before the request — leaves no window for a DNS answer that changes
    between the check and the connection.
    """

    def __init__(self) -> None:
        self._inner = DefaultResolver()

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET
    ) -> list[ResolveResult]:
        results = await self._inner.resolve(host, port, family)
        public = [r for r in results if is_public_address(r["host"])]
        if not public:
            raise _BlockedAddress(f"{host} ne résout vers aucune adresse publique")
        return public

    async def close(self) -> None:
        await self._inner.close()


async def _count_response(_session: Any, _ctx: Any, params: TraceRequestEndParams) -> None:
    health.record_http(params.url.host or "", params.response.status)


async def _count_failure(_session: Any, _ctx: Any, params: TraceRequestExceptionParams) -> None:
    health.record_http(params.url.host or "", None)


def _health_trace() -> TraceConfig:
    """Request hooks feeding :data:`src.core.health.health`, one record per attempt."""
    trace = TraceConfig()
    trace.on_request_end.append(_count_response)
    trace.on_request_exception.append(_count_failure)
    return trace


class HttpClient:
    """Lazy singleton handing out one ``aiohttp.ClientSession`` per event loop.

    aiohttp sessions are bound to the loop they are created on, so sharing a
    single session between the bot loop and the Web UI's uvicorn loop (daemon
    thread) breaks — same constraint as ``src.core.db.MongoManager``. In
    practice there are at most two sessions. Callers should not close them —
    :meth:`close` is intended for shutdown hooks.
    """

    _instance: HttpClient | None = None
    _sessions: ClassVar[dict[asyncio.AbstractEventLoop, ClientSession]] = {}
    # Separate pool for fetch_untrusted(): its connector only reaches public addresses.
    _untrusted_sessions: ClassVar[dict[asyncio.AbstractEventLoop, ClientSession]] = {}
    # ClientSession construction is synchronous, so a thread lock is enough to
    # serialize creation across loops/threads (an asyncio.Lock would itself be
    # bound to a single loop).
    _create_lock: ClassVar[threading.Lock] = threading.Lock()

    def __new__(cls) -> HttpClient:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def session(self) -> ClientSession:
        """Return the calling loop's shared session, creating it on first use."""
        loop = asyncio.get_running_loop()
        session = self._sessions.get(loop)
        if session is None or session.closed:
            with self._create_lock:
                session = self._sessions.get(loop)
                if session is None or session.closed:
                    session = ClientSession(
                        timeout=_DEFAULT_TIMEOUT,
                        headers={"User-Agent": _DEFAULT_USER_AGENT},
                        trace_configs=[_health_trace()],
                    )
                    self._sessions[loop] = session
                    logger.info(
                        "Shared aiohttp ClientSession created (%d active).", len(self._sessions)
                    )
        return session

    async def untrusted_session(self) -> ClientSession:
        """The calling loop's session for :func:`fetch_untrusted` (public addresses only)."""
        loop = asyncio.get_running_loop()
        session = self._untrusted_sessions.get(loop)
        if session is None or session.closed:
            with self._create_lock:
                session = self._untrusted_sessions.get(loop)
                if session is None or session.closed:
                    session = ClientSession(
                        connector=TCPConnector(resolver=_PublicOnlyResolver()),
                        timeout=UNTRUSTED_TIMEOUT,
                        headers={"User-Agent": _DEFAULT_USER_AGENT},
                        trace_configs=[_health_trace()],
                    )
                    self._untrusted_sessions[loop] = session
        return session

    async def close(self) -> None:
        """Close the calling loop's sessions. Call this during graceful shutdown.

        Sessions owned by other loops must be closed from their own loop;
        in practice they belong to daemon threads that die with the process.
        """
        loop = asyncio.get_running_loop()
        for pool in (self._sessions, self._untrusted_sessions):
            session = pool.pop(loop, None)
            if session is not None and not session.closed:
                await session.close()
        logger.info("Shared aiohttp ClientSession(s) closed.")


# Global singleton — import and reuse everywhere.
http_client = HttpClient()

# Cap on a single backoff sleep so high retry counts can't stall a task.
_MAX_BACKOFF_SECONDS = 30.0


def _backoff_delay(pause: float, attempt: int) -> float:
    """Exponential backoff with jitter: ``pause * 2**attempt`` capped at 30s."""
    return min(pause * 2.0**attempt, _MAX_BACKOFF_SECONDS) + random.uniform(0, pause)


async def fetch(
    url: str,
    return_type: str = "text",
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    retries: int = 3,
    pause: int = 1,
) -> Any:
    """GET *url* with retry.

    - Retries up to *retries* times on 5xx responses, ``ClientError``, and
      ``asyncio.TimeoutError``. Exponential backoff with jitter:
      ``pause * 2**attempt`` (capped at 30s) plus up to *pause* of jitter.
    - A non-500 non-200 response is not retried.
    - On final failure raises :class:`src.core.errors.HttpError` (subclass of
      :class:`Exception`, so existing ``except Exception:`` sites still catch).

    Parameters
    ----------
    return_type:
        ``"text"`` or ``"json"``.
    headers, params:
        Merged into the session defaults (``headers`` override the default
        User-Agent if supplied).
    """
    if return_type not in ("text", "json"):
        raise ValueError("Invalid return_type. Expected 'text' or 'json'.")

    session = await http_client.session()
    merged_headers = {"User-Agent": _DEFAULT_USER_AGENT}
    if headers:
        merged_headers.update(headers)

    safe_url = redact_url(url)
    last_status: int | None = None
    for attempt in range(retries):
        try:
            async with session.get(url, headers=merged_headers, params=params) as response:
                last_status = response.status
                if response.status >= 500:
                    logger.error("Failed to fetch %s: Status %s", safe_url, response.status)
                    if attempt < retries - 1:
                        await asyncio.sleep(_backoff_delay(pause, attempt))
                        continue
                    raise HttpError(
                        f"Failed to fetch {safe_url}",
                        url=url,
                        status=response.status,
                    )
                if response.status != 200:
                    logger.error("Failed to fetch %s: Status %s", safe_url, response.status)
                    raise HttpError(
                        f"Failed to fetch {safe_url}",
                        url=url,
                        status=response.status,
                    )
                if return_type == "text":
                    return await response.text()
                return await response.json()
        except (TimeoutError, ClientError) as e:
            logger.error("Error fetching %s: %s", safe_url, e)
            if attempt == retries - 1:
                raise HttpError(
                    f"Error fetching {safe_url}: {e}",
                    url=url,
                    status=last_status,
                ) from e
            await asyncio.sleep(_backoff_delay(pause, attempt))

    # Loop exits only on repeated retryable 5xx without raising — guard rail.
    raise HttpError(f"Failed to fetch {safe_url}", url=url, status=last_status)


def check_untrusted_url(url: str) -> None:
    """Raise :class:`BlockedUrlError` unless *url* is http(s) with a usable host.

    A host written as an IP literal must be public (aiohttp never resolves
    those, so the resolver can't catch them). Host names are left to the
    resolver, which sees the addresses actually connected to.
    """
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError as e:
        raise BlockedUrlError("URL invalide", url=url) from e
    if parts.scheme not in ("http", "https"):
        raise BlockedUrlError("Seules les URL http(s) sont acceptées", url=url)
    if not host:
        raise BlockedUrlError("URL sans hôte", url=url)
    if host_is_ip_literal(host) and not is_public_address(host.strip("[]")):
        raise BlockedUrlError("Adresse non publique refusée", url=url)


async def _read_capped(response: ClientResponse, max_bytes: int, safe_url: str) -> bytes:
    too_big = f"Réponse trop volumineuse (> {max_bytes // 1024} Ko) : {safe_url}"
    if response.content_length is not None and response.content_length > max_bytes:
        raise HttpError(too_big, url=safe_url, status=response.status)
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.content.iter_chunked(64 * 1024):
        size += len(chunk)
        if size > max_bytes:
            raise HttpError(too_big, url=safe_url, status=response.status)
        chunks.append(chunk)
    return b"".join(chunks)


async def fetch_untrusted(
    url: str,
    return_type: str = "text",
    *,
    max_bytes: int = UNTRUSTED_MAX_BYTES,
    headers: dict[str, str] | None = None,
) -> Any:
    """GET a URL someone else chose (a guild's RSS feed…), safely.

    Unlike :func:`fetch`, which serves the bot's own configured endpoints:

    - only ``http``/``https``, and only public addresses: loopback, private
      networks (the MongoDB container, sibling services), link-local cloud
      metadata endpoints and the like are refused, after every redirect too
      (:class:`BlockedUrlError`);
    - the body is capped at *max_bytes*;
    - one attempt, :data:`UNTRUSTED_TIMEOUT` overall, at most
      :data:`UNTRUSTED_MAX_REDIRECTS` redirects — a poller just tries again
      next cycle.

    Raises :class:`~src.core.errors.HttpError` (or its subclass
    :class:`BlockedUrlError`) on any failure.
    """
    if return_type not in ("text", "json"):
        raise ValueError("Invalid return_type. Expected 'text' or 'json'.")
    session = await http_client.untrusted_session()
    merged_headers = {"User-Agent": _DEFAULT_USER_AGENT, **(headers or {})}
    current = url
    for _ in range(UNTRUSTED_MAX_REDIRECTS + 1):
        check_untrusted_url(current)
        safe_url = redact_url(current)
        try:
            async with session.get(
                current, headers=merged_headers, allow_redirects=False
            ) as response:
                if response.status in _REDIRECT_STATUSES:
                    location = response.headers.get("Location")
                    if not location:
                        raise HttpError(
                            f"Redirection sans destination : {safe_url}",
                            url=current,
                            status=response.status,
                        )
                    current = urljoin(current, location)
                    continue
                if response.status != 200:
                    raise HttpError(
                        f"Failed to fetch {safe_url}", url=current, status=response.status
                    )
                body = await _read_capped(response, max_bytes, safe_url)
                text = body.decode(response.charset or "utf-8", errors="replace")
        except ClientConnectorError as e:
            if isinstance(e.os_error, _BlockedAddress):
                raise BlockedUrlError("Adresse non publique refusée", url=current) from e
            raise HttpError(f"Error fetching {safe_url}: {e}", url=current) from e
        except (TimeoutError, ClientError) as e:
            raise HttpError(f"Error fetching {safe_url}: {e}", url=current) from e
        if return_type == "text":
            return text
        try:
            return json.loads(text)
        except ValueError as e:
            raise HttpError(f"Réponse JSON invalide : {safe_url}", url=current) from e
    raise HttpError(f"Trop de redirections : {redact_url(url)}", url=url)


__all__ = [
    "UNTRUSTED_MAX_BYTES",
    "BlockedUrlError",
    "HttpClient",
    "check_untrusted_url",
    "fetch",
    "fetch_untrusted",
    "http_client",
]
