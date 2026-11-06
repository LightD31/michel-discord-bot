"""What a dashboard user may do in one guild, read live from the bot.

The OAuth login hands over the user's guild list with their permissions *at
login time*. Trusting that snapshot for authorization means a member stripped
of ``MANAGE_GUILD``, or banned, keeps managing the guild's config until their
session expires. Per-guild checks therefore read the member through the bot,
whose cache the gateway keeps current (``GUILD_MEMBERS`` intent), and
:class:`~src.webui.context.WebUIContext` caches the answer briefly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.core.permissions import can_manage_guild


@dataclass(frozen=True)
class GuildAccess:
    """A user's standing in one guild: membership, ownership, effective permissions."""

    is_member: bool = False
    is_owner: bool = False
    permissions: int = 0

    @property
    def manages_guild(self) -> bool:
        return self.is_member and can_manage_guild(self.permissions, is_owner=self.is_owner)


NO_ACCESS = GuildAccess()


async def read_guild_access(bot: Any, guild_id: str | int, user_id: str | int) -> GuildAccess:
    """Resolve *user_id*'s standing in *guild_id*. Must run on the bot loop.

    The guild comes from the cache only: the gateway puts every guild the bot is
    in there, so a miss means the bot is not in it (an API lookup would only
    answer 403). The member is cache-first, API on a miss. A guild without the
    bot, or a user who is not a member, yields :data:`NO_ACCESS`.
    """
    guild = bot.get_guild(int(guild_id))
    if guild is None:
        return NO_ACCESS
    member = await guild.fetch_member(int(user_id))
    if member is None:
        return NO_ACCESS
    return GuildAccess(
        is_member=True,
        is_owner=bool(guild.is_owner(member.id)),
        permissions=int(member.guild_permissions),
    )


__all__ = ["NO_ACCESS", "GuildAccess", "read_guild_access"]
