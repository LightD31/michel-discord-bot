"""LLDAP Discord extension.

Lets members holding a mapped role create their own LLDAP account
(``/register``) and keeps LLDAP group membership in line with Discord roles
(every 10 minutes, or ``/lldap sync``). Each guild configures its own LLDAP
server in ``moduleLldap``. The LLDAP calls live in ``src.integrations.lldap``,
the field rules and sync planning in ``features.lldap``; this package wires
them to Discord: lifecycle here, commands in ``commands``, the sync in ``sync``.
"""

import asyncio

from interactions import Client, Extension, listen

from src.integrations.lldap import LldapClient

from .commands import CommandsMixin
from .sync import SyncMixin


class LldapExtension(Extension, CommandsMixin, SyncMixin):
    """Discord extension that creates LLDAP accounts and syncs groups with roles."""

    def __init__(self, bot: Client):
        self.bot: Client = bot
        self.lldap = LldapClient()
        self._sync_lock = asyncio.Lock()
        # One registration at a time: the "already linked" check and the creation must not race.
        self._register_lock = asyncio.Lock()
        # Instance keys of the LLDAP servers currently failing, to log an outage once.
        self._failing: set[str] = set()
        self._warned: set[str] = set()

    @listen()
    async def on_startup(self):
        """Start the sync loop with an immediate first run."""
        if not self.sync_roles.running:
            self.sync_roles.start()
        await self.sync_roles()


def setup(bot: Client) -> None:
    LldapExtension(bot)
