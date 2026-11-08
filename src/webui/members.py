"""The member list behind the dashboard's member pickers, searched and paged here.

A guild can count tens of thousands of members, and the dashboard never takes
them all: it asks for one page of a search (``?q=``), or for the few members it
has to label (``?ids=``). The full list is built on the bot loop by
:func:`load_members` (the ``users`` collection userinfo keeps, completed by the
gateway's member cache) and a :class:`MemberDirectory` keeps it a minute per
guild, so typing in a picker doesn't read MongoDB on each keystroke.
"""

from __future__ import annotations

import time
import unicodedata
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from features.userinfo.repository import UserInfoRepository
from src.core import logging as logutil
from src.core.errors import DatabaseError

logger = logutil.init_logger("webui.members")

DEFAULT_PAGE = 50
MAX_PAGE = 100
#: Most ids one ``?ids=`` request resolves (a namemap with that many rows is rare).
MAX_IDS = 200
CACHE_TTL_SECONDS = 60.0


@dataclass(frozen=True)
class Member:
    id: str
    username: str
    display_name: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "username": self.username, "display_name": self.display_name}


def fold(text: str) -> str:
    """*text* without case or accents, so ``"elo"`` finds ``"Élodie"``."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def search(
    members: Sequence[Member],
    query: str,
    *,
    names: Mapping[str, Any] | None = None,
    limit: int = DEFAULT_PAGE,
    offset: int = 0,
) -> tuple[list[Member], int]:
    """One page of the members matching *query*, and how many match in all.

    A member matches when the query is part of their display name, username,
    id, or the first name *names* gives them (the guild's ``discord2name``).
    Those whose name starts with the query come first; otherwise the list keeps
    its order. An empty query matches everyone.
    """
    needle = fold(query.strip())
    if not needle:
        return list(members[offset : offset + limit]), len(members)
    names = names or {}
    starts: list[Member] = []
    contains: list[Member] = []
    for member in members:
        fields = [fold(member.display_name), fold(member.username), member.id]
        prenom = names.get(member.id)
        if isinstance(prenom, str) and prenom:
            fields.append(fold(prenom))
        if any(f.startswith(needle) for f in fields):
            starts.append(member)
        elif any(needle in f for f in fields):
            contains.append(member)
    matches = starts + contains
    return matches[offset : offset + limit], len(matches)


def pick(members: Iterable[Member], ids: Iterable[str]) -> list[Member]:
    """The members among *members* whose id is in *ids*, in *ids* order."""
    by_id = {m.id: m for m in members}
    return [by_id[i] for i in dict.fromkeys(ids) if i in by_id]


async def load_members(bot: Any, guild_id: str) -> list[Member]:
    """Every member the bot knows in *guild_id*, sorted by display name.

    Runs on the bot loop. The ``users`` collection comes first; the gateway's
    member cache adds whoever userinfo hasn't stored yet (bots left out).
    """
    found: dict[str, Member] = {}
    try:
        for row in await UserInfoRepository(guild_id).all_members():
            uid = row["id"]
            found[uid] = Member(uid, row["username"], row["display_name"] or row["username"] or uid)
    except DatabaseError as e:
        logger.warning("Could not read the users of guild %s: %s", guild_id, e)

    try:
        guild = await bot.fetch_guild(int(guild_id))
    except Exception as e:  # noqa: BLE001 — the stored list is still worth returning
        logger.debug("Guild %s not available for its member cache: %s", guild_id, e)
        guild = None
    for m in getattr(guild, "members", None) or []:
        uid = str(m.id)
        if uid in found or getattr(m, "bot", False):
            continue
        username = getattr(m, "username", "") or ""
        display = getattr(m, "display_name", None) or username or uid
        found[uid] = Member(uid, username, display)

    return sorted(found.values(), key=lambda m: fold(m.display_name or m.username))


class MemberDirectory:
    """Each guild's member list, loaded on demand and kept :data:`CACHE_TTL_SECONDS`.

    Used from the dashboard's loop only. Two requests racing on a cold guild
    may both load it; the second result simply replaces the first.
    """

    def __init__(
        self,
        load: Callable[[str], Awaitable[list[Member]]],
        *,
        ttl: float = CACHE_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._load = load
        self._ttl = ttl
        self._clock = clock
        self._cache: dict[str, tuple[float, list[Member]]] = {}

    async def members(self, guild_id: str) -> list[Member]:
        cached = self._cache.get(guild_id)
        now = self._clock()
        if cached is not None and now - cached[0] < self._ttl:
            return cached[1]
        members = await self._load(guild_id)
        self._cache[guild_id] = (self._clock(), members)
        return members


__all__ = [
    "DEFAULT_PAGE",
    "MAX_IDS",
    "MAX_PAGE",
    "Member",
    "MemberDirectory",
    "fold",
    "load_members",
    "pick",
    "search",
]
