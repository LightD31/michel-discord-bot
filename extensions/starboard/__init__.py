"""Starboard Discord extension — mirror highly-reacted messages to a dedicated channel.

Listens to :class:`MessageReactionAdd` / :class:`MessageReactionRemove`. When a
message accumulates the configured number of star reactions, it is republished
as an embed in the configured starboard channel. Subsequent reactions update
the count; if ``removeBelowThreshold`` is set, dropping below the threshold
deletes the mirror.

Per-guild config: ``moduleStarboard`` (channel, emoji, threshold, options).
Persistent state lives in :class:`features.starboard.StarboardRepository`.
"""

import asyncio

from interactions import Client, Extension, listen

from features.starboard import StarboardRepository

from ._common import enabled_servers, logger
from .listeners import ListenersMixin


class StarboardExtension(Extension, ListenersMixin):
    def __init__(self, bot: Client):
        self.bot = bot
        self.lock = asyncio.Lock()
        self._repos: dict[str, StarboardRepository] = {}

    def repository(self, guild_id: str | int) -> StarboardRepository:
        gid = str(guild_id)
        repo = self._repos.get(gid)
        if repo is None:
            repo = StarboardRepository(gid)
            self._repos[gid] = repo
        return repo

    @listen()
    async def on_startup(self):
        for gid in enabled_servers:
            await self.repository(gid).ensure_indexes()
        logger.info("Starboard ready (%d guild(s))", len(enabled_servers))


def setup(bot: Client) -> None:
    StarboardExtension(bot)


__all__ = ["StarboardExtension", "setup"]
