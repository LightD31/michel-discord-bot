"""Reaction Roles Discord extension — self-assignable roles via persistent buttons.

Slash commands (admin-only):
- ``/rolemenu create`` — create a menu in a channel with up to 25 (role, emoji, label) entries
- ``/rolemenu list`` — list role menus on this server
- ``/rolemenu delete`` — remove a menu (deletes the message + Mongo doc)
- ``/rolemenu edit`` — change title/description of an existing menu

Buttons are persistent: their callbacks rebind on extension load, so menus
keep working across restarts. Per-guild config: ``moduleReactionRoles``.
"""

from interactions import Client, Extension, listen

from features.reactionroles import ReactionRolesRepository

from ._common import enabled_servers, logger
from .buttons import ButtonsMixin
from .commands import CommandsMixin


class ReactionRolesExtension(Extension, CommandsMixin, ButtonsMixin):
    def __init__(self, bot: Client):
        self.bot = bot
        self._repos: dict[str, ReactionRolesRepository] = {}

    def repository(self, guild_id: str | int) -> ReactionRolesRepository:
        gid = str(guild_id)
        repo = self._repos.get(gid)
        if repo is None:
            repo = ReactionRolesRepository(gid)
            self._repos[gid] = repo
        return repo

    @listen()
    async def on_startup(self):
        for gid in enabled_servers:
            await self.repository(gid).ensure_indexes()
        logger.info("Reaction Roles ready (%d guild(s))", len(enabled_servers))


def setup(bot: Client) -> None:
    ReactionRolesExtension(bot)


__all__ = ["ReactionRolesExtension", "setup"]
