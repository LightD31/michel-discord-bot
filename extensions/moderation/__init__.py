"""Moderation Discord extension — member actions, infraction history, automod.

Slash commands: ``/warn``, ``/timeout``, ``/untimeout``, ``/kick``, ``/ban``,
``/unban`` and ``/infraction list|view|remove``. Automod (anti-invite, anti-spam,
banned-words) runs via a ``MessageCreate`` listener. Per-guild config:
``moduleModeration`` (modlog channel, staff role, automod toggles).
"""

from collections import deque

from interactions import Client, Extension, listen

from features.moderation import ModerationRepository

from ._common import enabled_servers, logger
from .automod import AutomodMixin
from .commands import CommandsMixin
from .logging_ import ModLogMixin


class ModerationExtension(Extension, CommandsMixin, AutomodMixin, ModLogMixin):
    def __init__(self, bot: Client):
        self.bot = bot
        self._repos: dict[str, ModerationRepository] = {}
        self._spam: dict[tuple[str, str], deque] = {}

    def repository(self, guild_id: str | int) -> ModerationRepository:
        gid = str(guild_id)
        repo = self._repos.get(gid)
        if repo is None:
            repo = ModerationRepository(gid)
            self._repos[gid] = repo
        return repo

    @listen()
    async def on_startup(self):
        for gid in enabled_servers:
            await self.repository(gid).ensure_indexes()
        logger.info("Moderation ready (%d guild(s))", len(enabled_servers))


def setup(bot: Client) -> None:
    ModerationExtension(bot)


__all__ = ["ModerationExtension", "setup"]
