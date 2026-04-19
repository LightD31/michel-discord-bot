"""Utility Extension package for Discord Bot.

Assembles :class:`UtilExtension` from focused mixin modules:

- :mod:`.commands`  — ping, delete, send
- :mod:`.polls`     — poll creation, editing, reaction tracking
- :mod:`.reminders` — reminder scheduling and background task

Shared logger, config, and constants live in :mod:`._common`.
"""

import asyncio

from interactions import Client, Extension, listen

from ._common import enabled_servers
from .commands import CommandsMixin
from .polls import PollsMixin
from .reminders import RemindersMixin

from features.reminders import ReminderRepository


class UtilExtension(CommandsMixin, PollsMixin, RemindersMixin, Extension):
    def __init__(self, bot: Client):
        self.bot: Client = bot
        self.lock = asyncio.Lock()
        self._reminder_repos: dict[str, ReminderRepository] = {}

    @listen()
    async def on_startup(self):
        for guild_id in enabled_servers:
            await self._reminder_repo(guild_id).ensure_indexes()
        self.check_reminders.start()


def setup(bot: Client):
    UtilExtension(bot)
