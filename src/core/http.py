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
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

from src.core import logging as _logging
from src.core.errors import HttpError

logger = _logging.init_logger(os.path.basename(__file__))

# Default User-Agent kept identical to the previous ``src/utils.fetch`` impl
# so servers that gate on UA keep accepting our requests.
_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
)
_DEFAULT_TIMEOUT = ClientTimeout(total=30)


class HttpClient:
    """Lazy singleton around a single ``aiohttp.ClientSession``.

    The session is created on first use inside the running event loop. Callers
    should not close it — :meth:`close` is intended for shutdown hooks.
    """

    _instance: HttpClient | None = None

    def __new__(cls) -> HttpClient:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._session = None  # type: ignore[attr-defined]
            cls._instance._lock = asyncio.Lock()  # type: ignore[attr-defined]
        return cls._instance

    async def session(self) -> ClientSession:
        """Return the shared session, creating it on first use."""
        if self._session is None or self._session.closed:
            async with self._lock:
                if self._session is None or self._session.closed:
                    self._session = ClientSession(
                        timeout=_DEFAULT_TIMEOUT,
                        headers={"User-Agent": _DEFAULT_USER_AGENT},
                    )
                    logger.info("Shared aiohttp ClientSession created.")
        return self._session

    async def close(self) -> None:
        """Close the shared session. Call this during graceful shutdown."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
            logger.info("Shared aiohttp ClientSession closed.")
        self._session = None


# Global singleton — import and reuse everywhere.
http_client = HttpClient()


async def fetch(
    url: str,
    return_type: str = "text",
    headers: dict | None = None,
    params: dict | None = None,
    retries: int = 3,
    pause: int = 1,
) -> Any:
    """GET *url* with retry.

    Behaviour intentionally matches the legacy ``src.utils.fetch``:

    - Retries up to *retries* times on 5xx responses, ``ClientError``, and
      ``asyncio.TimeoutError``. Linear backoff: ``pause * (attempt + 1)``.
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

    last_status: int | None = None
    for attempt in range(retries):
        try:
            async with session.get(url, headers=merged_headers, params=params) as response:
                last_status = response.status
                if response.status >= 500:
                    logger.error("Failed to fetch %s: Status %s", url, response.status)
                    if attempt < retries - 1:
                        await asyncio.sleep(pause * (attempt + 1))
                        continue
                    raise HttpError(
                        f"Failed to fetch {url}",
                        url=url,
                        status=response.status,
                    )
                if response.status != 200:
                    logger.error("Failed to fetch %s: Status %s", url, response.status)
                    raise HttpError(
                        f"Failed to fetch {url}",
                        url=url,
                        status=response.status,
                    )
                if return_type == "text":
                    return await response.text()
                return await response.json()
        except (TimeoutError, ClientError) as e:
            logger.error("Error fetching %s: %s", url, e)
            if attempt == retries - 1:
                raise HttpError(
                    f"Error fetching {url}: {e}",
                    url=url,
                    status=last_status,
                ) from e
            await asyncio.sleep(pause)

    # Loop exits only on repeated retryable 5xx without raising — guard rail.
    raise HttpError(f"Failed to fetch {url}", url=url, status=last_status)


__all__ = ["HttpClient", "fetch", "http_client"]
