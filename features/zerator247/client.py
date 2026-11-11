"""HTTP client for the ZeratoR 24/7 site's public API."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from src.core import logging as logutil
from src.core.errors import HttpError, IntegrationError
from src.core.http import fetch_untrusted
from src.core.urls import redact_url

from .models import Cycle

logger = logutil.init_logger(__name__)


class Zerator247Error(IntegrationError):
    """The 24/7 site could not be read. The message is user-facing (French)."""


async def _fetch_json(url: str) -> Any:
    return await fetch_untrusted(url, return_type="json")


class Zerator247Client:
    """Reads the vote cycle from one ZeratoR 24/7 site (``{base_url}/api/now``).

    The site is a guild setting, so it is read with
    :func:`~src.core.http.fetch_untrusted`: public addresses only, a capped
    body and a single attempt (the cycle is polled every minute anyway).
    """

    def __init__(
        self,
        base_url: str,
        get_json: Callable[[str], Awaitable[Any]] = _fetch_json,
    ):
        self.base_url = base_url
        self._get = get_json

    async def _get_json(self, path: str) -> Any:
        url = f"{self.base_url.rstrip('/')}/api/{path}"
        try:
            return await self._get(url)
        except HttpError as e:
            # Unreachable, an error status, a non-public address or invalid JSON.
            logger.warning("ZeratoR 24/7 unreachable (%s): %s", redact_url(url), e)
            raise Zerator247Error("ZeratoR 24/7 ne répond pas pour le moment.") from e

    async def get_cycle(self) -> Cycle | None:
        """The current vote cycle, or ``None`` when nothing is airing."""
        return Cycle.from_payload(await self._get_json("now"))
