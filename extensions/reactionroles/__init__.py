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

from ._common import REACTION_ROLES, logger
from .buttons import ButtonsMixin
from .commands import CommandsMixin


class ReactionRolesExtension(Extension, CommandsMixin, ButtonsMixin):
    MODULE_KEYS = ("moduleReactionRoles",)

    def __init__(self, bot: Client):
        self.bot = bot

    def repository(self, guild_id: str | int) -> ReactionRolesRepository:
        return ReactionRolesRepository(str(guild_id))

    @listen()
    async def on_startup(self):
        for gid in REACTION_ROLES.guild_ids():
            await self.repository(gid).ensure_indexes()
        logger.info("Reaction Roles ready (%d guild(s))", len(REACTION_ROLES.guild_ids()))


def setup(bot: Client) -> None:
    ReactionRolesExtension(bot)


__all__ = ["ReactionRolesExtension", "setup"]
