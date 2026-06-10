"""Shared HTTP client for extensions.

Replaces the per-call ``aiohttp.ClientSession`` that lived in
``src/utils.fetch()``. A single long-lived session is reused across the whole
process, which:

- Reuses TCP connections (DNS + TLS handshake amortized).
- Has a single place to set defaults (timeouts, headers, user-agent).
- Simplifies future work (proxies, per-host rate limiting, metrics).

For backward compatibility ``src.utils.fetch`` now delegates here and keeps
its original signature.

Usage::

    from src.core.http import fetch

    body = await fetch("https://example.com/api", return_type="json")
"""

from __future__ import annotations

import asyncio
import os
import random
import threading
from typing import Any, ClassVar
from urllib.parse import urlsplit, urlunsplit

from aiohttp import ClientError, ClientSession, ClientTimeout

from src.core import logging as _logging
from src.core.errors import HttpError

logger = _logging.init_logger(os.path.basename(__file__))

_SENSITIVE_QUERY_KEYS = ("token", "access_token", "key", "api_key", "apikey", "password", "secret")


def _redact_url(url: str) -> str:
    """Strip userinfo and known credential-carrying query params from *url*.

    Used before logging so credentials passed in the URL never end up on disk.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparseable url>"
    netloc = parts.hostname or ""
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    if parts.username:
        netloc = f"***@{netloc}"
    query = parts.query
    if query:
        kept = []
        for pair in query.split("&"):
            key, sep, _ = pair.partition("=")
            if key.lower() in _SENSITIVE_QUERY_KEYS:
                kept.append(f"{key}{sep}***" if sep else key)
            else:
                kept.append(pair)
        query = "&".join(kept)
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


# Default User-Agent kept identical to the previous ``src/utils.fetch`` impl
# so servers that gate on UA keep accepting our requests.
_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
)
_DEFAULT_TIMEOUT = ClientTimeout(total=30)


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
                    )
                    self._sessions[loop] = session
                    logger.info(
                        "Shared aiohttp ClientSession created (%d active).", len(self._sessions)
                    )
        return session

    async def close(self) -> None:
        """Close the calling loop's session. Call this during graceful shutdown.

        Sessions owned by other loops must be closed from their own loop;
        in practice they belong to daemon threads that die with the process.
        """
        session = self._sessions.pop(asyncio.get_running_loop(), None)
        if session is not None and not session.closed:
            await session.close()
            logger.info("Shared aiohttp ClientSession closed.")


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

    Behaviour intentionally matches the legacy ``src.utils.fetch``:

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

    safe_url = _redact_url(url)
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


__all__ = ["HttpClient", "fetch", "http_client"]
