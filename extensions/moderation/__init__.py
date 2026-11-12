"""Moderation Discord extension — member actions, infraction history, automod.

Slash commands: ``/warn``, ``/timeout``, ``/untimeout``, ``/kick``, ``/ban``,
``/unban`` and ``/infraction list|view|remove``. Automod (anti-invite, anti-spam,
banned-words) runs via a ``MessageCreate`` listener. Per-guild config:
``moduleModeration`` (modlog channel, staff role, automod toggles).
"""

from interactions import Client, Extension, listen

from features.moderation import ModerationRepository, SpamTracker

from ._common import MODERATION, logger
from .automod import AutomodMixin
from .commands import CommandsMixin
from .logging_ import ModLogMixin


class ModerationExtension(Extension, CommandsMixin, AutomodMixin, ModLogMixin):
    MODULE_KEYS = ("moduleModeration",)

    def __init__(self, bot: Client):
        self.bot = bot
        self._spam = SpamTracker()

    def repository(self, guild_id: str | int) -> ModerationRepository:
        return ModerationRepository(str(guild_id))

    @listen()
    async def on_startup(self):
        for gid in MODERATION.guild_ids():
            await self.repository(gid).ensure_indexes()
        logger.info("Moderation ready (%d guild(s))", len(MODERATION.guild_ids()))


def setup(bot: Client) -> None:
    ModerationExtension(bot)


__all__ = ["ModerationExtension", "setup"]
