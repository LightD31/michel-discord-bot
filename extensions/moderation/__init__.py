"""Moderation Discord extension — member actions, infraction history, automod.

Slash commands: ``/avertir``, ``/exclure``, ``/lever-exclusion``, ``/expulser``, ``/bannir``,
``/debannir`` and ``/infraction liste|view|remove``. Right-click menus: "🚩 Signaler"
on a message (any member, ``reports.py``), "🛡️ Historique" and "⚠️ Avertir…" on
a member (moderators, ``menus.py``). Automod (anti-invite, anti-spam,
banned-words) runs via a ``MessageCreate`` listener. Per-guild config:
``moduleModeration`` (modlog channel, staff role, automod toggles).
"""

import asyncio

from interactions import Client, Extension, listen

from features.moderation import ModerationRepository, ReportLimiter, ReportRepository, SpamTracker

from ._common import MODERATION, logger
from .automod import AutomodMixin
from .commands import CommandsMixin
from .logging_ import ModLogMixin
from .menus import MenusMixin
from .reports import ReportsMixin


class ModerationExtension(
    Extension, CommandsMixin, AutomodMixin, ModLogMixin, ReportsMixin, MenusMixin
):
    MODULE_KEYS = ("moduleModeration",)

    def __init__(self, bot: Client):
        self.bot = bot
        self._spam = SpamTracker()
        self.report_limits = ReportLimiter()
        self.report_lock = asyncio.Lock()

    def repository(self, guild_id: str | int) -> ModerationRepository:
        return ModerationRepository(str(guild_id))

    def reports(self, guild_id: str | int) -> ReportRepository:
        return ReportRepository(str(guild_id))

    @listen()
    async def on_startup(self):
        for gid in MODERATION.guild_ids():
            await self.repository(gid).ensure_indexes()
            await self.reports(gid).ensure_indexes()
        logger.info("Moderation ready (%d guild(s))", len(MODERATION.guild_ids()))


def setup(bot: Client) -> None:
    ModerationExtension(bot)


__all__ = ["ModerationExtension", "setup"]
