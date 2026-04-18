"""User Info Extension — thin Discord glue layer.

All MongoDB I/O lives in features/userinfo/.
"""

import os

from interactions import Client, Extension, listen
from interactions.api.events import MemberAdd, MemberRemove, MemberUpdate

from src import logutil
from features.userinfo import UserInfoRepository

logger = logutil.init_logger(os.path.basename(__file__))


class UserInfoExtension(Extension):
    """Keeps a per-guild `users` collection up to date with Discord display names."""

    def __init__(self, bot: Client):
        self.bot: Client = bot

    @listen()
    async def on_startup(self):
        for guild in self.bot.guilds:
            repo = UserInfoRepository(str(guild.id))
            members_data = [
                {"id": str(m.id), "username": m.username, "display_name": m.display_name}
                for m in guild.members
                if not m.bot
            ]
            await repo.bulk_upsert(members_data)

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
