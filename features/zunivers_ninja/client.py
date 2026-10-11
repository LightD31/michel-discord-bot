"""HTTP client for the ZUnivers Ninja API (plans, strategies) and links to its web UI."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

from aiohttp import ClientError, ClientSession, ClientTimeout

from src.core import logging as logutil
from src.core.errors import IntegrationError
from src.core.http import http_client

logger = logutil.init_logger(__name__)

# Planning a large collection is a beam search: give it more room than the
# shared session's 30 s default.
_PLAN_TIMEOUT = ClientTimeout(total=90)
# Autocomplete must answer within 3 s.
_STRATEGIES_TIMEOUT = ClientTimeout(total=2)

RULESETS = ("NORMAL", "HARDCORE")

# The strategy list only changes when Ninja is upgraded; cache it so autocomplete
# doesn't hit Ninja on every keystroke.
_STRATEGIES_TTL = 600.0
_strategies_cache: dict[str, tuple[float, list[NinjaStrategy]]] = {}


class NinjaError(IntegrationError):
    """ZUnivers Ninja could not produce a plan. The message is user-facing (French)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class NinjaNotFoundError(NinjaError):
    """The pseudo is unknown to ZUnivers (HTTP 404)."""


@dataclass(frozen=True)
class NinjaAttachment:
    """Full command list, sent as a file when it does not fit in the embed."""

    name: str
    content: str


@dataclass(frozen=True)
class NinjaPlan:
    """A plan as returned by ``/api/plan/{pseudo}/discord``."""

    hash: str
    empty: bool
    command_count: int
    commands: list[str]
    content: str
    embeds: list[dict[str, Any]]
    attachment: NinjaAttachment | None
    player_name: str
    player_display_name: str
    discord_id: str | None

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> NinjaPlan:
        """Parse the route's JSON body."""
        try:
            player = data.get("player") or {}
            raw_attachment = data.get("attachment")
            attachment = (
                NinjaAttachment(
                    name=str(raw_attachment.get("name") or "conseils.txt"),
                    content=str(raw_attachment["content"]),
                )
                if isinstance(raw_attachment, dict)
                else None
            )
            commands = [str(c) for c in data.get("commands") or []]
            discord_id = player.get("discordId")
            return cls(
                hash=str(data["hash"]),
                empty=bool(data.get("empty", not commands)),
                command_count=int(data.get("commandCount", len(commands))),
                commands=commands,
                content=str(data.get("content") or ""),
                embeds=[e for e in data.get("embeds") or [] if isinstance(e, dict)],
                attachment=attachment,
                player_name=str(player.get("name") or ""),
                player_display_name=str(player.get("displayName") or player.get("name") or ""),
                discord_id=str(discord_id) if discord_id else None,
            )
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            raise NinjaError("Réponse inattendue de ZUnivers Ninja.") from e


@dataclass(frozen=True)
class NinjaStrategy:
    """A planner preset listed by ``GET /api/strategies`` (Ninja >= 2.3)."""

    id: str
    label: str
    description: str


def parse_strategies(data: Any) -> list[NinjaStrategy]:
    """Parse ``GET /api/strategies``; entries without an id are skipped."""
    raw = data.get("strategies") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        raise NinjaError("Réponse inattendue de ZUnivers Ninja.")
    return [
        NinjaStrategy(
            id=str(item["id"]),
            label=str(item.get("label") or item["id"]),
            description=str(item.get("description") or ""),
        )
        for item in raw
        if isinstance(item, dict) and item.get("id")
    ]


def build_plan_request(
    base_url: str,
    pseudo: str,
    *,
    ruleset: str | None = None,
    strategy: str | None = None,
) -> tuple[str, dict[str, str]]:
    """Return the URL and query parameters for a pseudo's Discord plan."""
    url = f"{base_url.rstrip('/')}/api/plan/{quote(pseudo.strip(), safe='')}/discord"
    params: dict[str, str] = {}
    if ruleset:
        params["ruleset"] = ruleset.upper()
    if strategy:
        params["strategy"] = strategy
    return url, params


def build_web_url(
    web_url: str, pseudo: str, *, hardcore: bool = False, strategy: str | None = None
) -> str:
    """Link to *pseudo*'s plan in the Ninja web UI (``?u=<pseudo>[&mode=hardcore][&strategy=]``)."""
    params = {"u": pseudo.strip()}
    if hardcore:
        params["mode"] = "hardcore"
    if strategy:
        params["strategy"] = strategy
    return f"{web_url.rstrip('/')}/?{urlencode(params)}"


async def _error_message(response: Any, fallback: str) -> str:
    """Ninja answers errors as ``{"error": "<message en français>"}``."""
    try:
        data = await response.json(content_type=None)
    except (ClientError, ValueError):
        return fallback
    if isinstance(data, dict) and isinstance(data.get("error"), str):
        return str(data["error"])
    return fallback


class NinjaClient:
    """Talks to one ZUnivers Ninja server."""

    def __init__(
        self,
        base_url: str,
        session_factory: Callable[[], Awaitable[ClientSession]] = http_client.session,
    ):
        self.base_url = base_url
        self._session_factory = session_factory

    async def _get_json(
        self, url: str, params: dict[str, str], *, not_found: str, timeout: ClientTimeout
    ) -> Any:
        """GET *url* and return its JSON body, raising :class:`NinjaError` on failure.

        Ninja answers errors as ``{"error": "<message en français>"}``; that message
        is kept. A 404 raises :class:`NinjaNotFoundError` (fallback message *not_found*).
        """
        session = await self._session_factory()
        try:
            async with session.get(url, params=params, timeout=timeout) as response:
                if response.status == 404:
                    raise NinjaNotFoundError(await _error_message(response, not_found), status=404)
                if response.status >= 400:
                    raise NinjaError(
                        await _error_message(
                            response, f"ZUnivers Ninja a répondu HTTP {response.status}."
                        ),
                        status=response.status,
                    )
                return await response.json(content_type=None)
        except (TimeoutError, ClientError) as e:
            logger.warning("ZUnivers Ninja unreachable (%s): %s", url, e)
            raise NinjaError("ZUnivers Ninja ne répond pas pour le moment.") from e
        except ValueError as e:
            raise NinjaError("Réponse inattendue de ZUnivers Ninja.") from e

    async def get_plan(
        self,
        pseudo: str,
        *,
        ruleset: str | None = None,
        strategy: str | None = None,
    ) -> NinjaPlan:
        """Fetch *pseudo*'s plan.

        Raises :class:`NinjaNotFoundError` for an unknown pseudo and
        :class:`NinjaError` for anything else that kept Ninja from answering
        (including an unknown strategy, which Ninja answers with a 400).
        """
        url, params = build_plan_request(self.base_url, pseudo, ruleset=ruleset, strategy=strategy)
        data = await self._get_json(
            url,
            params,
            not_found=f"Joueur « {pseudo} » introuvable.",
            timeout=_PLAN_TIMEOUT,
        )
        if not isinstance(data, dict):
            raise NinjaError("Réponse inattendue de ZUnivers Ninja.")
        return NinjaPlan.from_payload(data)

    async def get_strategies(self) -> list[NinjaStrategy]:
        """Strategies the server offers, cached for 10 minutes per server.

        An older Ninja without ``/api/strategies`` yields an empty list. When Ninja
        can't be reached, the last list fetched is returned if there is one.
        """
        cached = _strategies_cache.get(self.base_url)
        if cached and time.monotonic() - cached[0] < _STRATEGIES_TTL:
            return cached[1]
        try:
            data = await self._get_json(
                f"{self.base_url.rstrip('/')}/api/strategies",
                {},
                not_found="",
                timeout=_STRATEGIES_TIMEOUT,
            )
            strategies = parse_strategies(data)
        except NinjaNotFoundError:
            strategies = []
        except NinjaError:
            if cached:
                return cached[1]
            raise
        _strategies_cache[self.base_url] = (time.monotonic(), strategies)
        return strategies
