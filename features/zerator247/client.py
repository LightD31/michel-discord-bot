"""HTTP client for the ZeratoR 24/7 site's public API."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

from src.core import logging as logutil
from src.core.errors import IntegrationError
from src.core.http import http_client

from .models import Cycle

logger = logutil.init_logger(__name__)

# Polled every minute: a slow answer is better skipped than stacked.
_TIMEOUT = ClientTimeout(total=10)


class Zerator247Error(IntegrationError):
    """The 24/7 site could not be read. The message is user-facing (French)."""


class Zerator247Client:
    """Reads the vote cycle from one ZeratoR 24/7 site (``{base_url}/api/now``)."""

    def __init__(
        self,
        base_url: str,
        session_factory: Callable[[], Awaitable[ClientSession]] = http_client.session,
    ):
        self.base_url = base_url
        self._session_factory = session_factory

    async def _get_json(self, path: str) -> Any:
        url = f"{self.base_url.rstrip('/')}/api/{path}"
        session = await self._session_factory()
        try:
            async with session.get(url, timeout=_TIMEOUT) as response:
                if response.status >= 400:
                    raise Zerator247Error(f"ZeratoR 24/7 a répondu HTTP {response.status}.")
                return await response.json(content_type=None)
        except (TimeoutError, ClientError) as e:
            logger.warning("ZeratoR 24/7 unreachable (%s): %s", url, e)
            raise Zerator247Error("ZeratoR 24/7 ne répond pas pour le moment.") from e
        except ValueError as e:
            raise Zerator247Error("Réponse inattendue de ZeratoR 24/7.") from e

    async def get_cycle(self) -> Cycle | None:
        """The current vote cycle, or ``None`` when nothing is airing."""
        return Cycle.from_payload(await self._get_json("now"))
