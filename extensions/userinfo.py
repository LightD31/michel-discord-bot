"""User Info Extension — thin Discord glue layer.

All MongoDB I/O lives in features/userinfo/.
"""

import os

from interactions import Client, Extension, listen
from interactions.api.events import MemberAdd, MemberRemove, MemberUpdate

from features.userinfo import UserInfoRepository
from src.core import logging as logutil
from src.core.errors import DatabaseError

logger = logutil.init_logger(__name__)


class UserInfoExtension(Extension):
    """Keeps a per-guild `users` collection up to date with Discord display names."""

    MODULE_KEYS = ()

    def __init__(self, bot: Client):
        self.bot: Client = bot

    @listen()
    async def on_startup(self):
        for guild in self.bot.guilds:
            repo = UserInfoRepository(str(guild.id))
            members = list(guild.members)
            members_data = [
                {"id": str(m.id), "username": m.username, "display_name": m.display_name}
                for m in members
                if not m.bot
            ]
            await repo.bulk_upsert(members_data)
            # Members who left while the bot was down never sent a MemberRemove:
            # drop them too, but only from a complete member list (a partial
            # one would delete members the cache just doesn't hold yet).
            member_count = getattr(guild, "member_count", 0)
            if not member_count or len(members) < member_count:
                continue
            try:
                removed = await repo.delete_absent([d["id"] for d in members_data])
            except DatabaseError as e:
                logger.warning("Departed members of %s not removed: %s", guild.id, e)
                continue
            if removed:
                logger.info("Removed %d departed member(s) of guild %s", removed, guild.id)

    @listen()
    async def on_member_add(self, event: MemberAdd):
        member = event.member
        if member.bot:
            return
        repo = UserInfoRepository(str(event.guild_id))
        await repo.upsert(str(member.id), member.username, member.display_name)

    @listen()
    async def on_member_remove(self, event: MemberRemove):
        member = event.member
        if member.bot:
            return
        repo = UserInfoRepository(str(event.guild_id))
        await repo.delete(str(member.id))

    @listen()
    async def on_member_update(self, event: MemberUpdate):
        member = event.after
        if member.bot:
            return
        repo = UserInfoRepository(str(event.guild_id))
        await repo.upsert(str(member.id), member.username, member.display_name)
