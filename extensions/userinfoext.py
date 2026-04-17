"""
User Info Extension

Maintains a per-guild `users` collection that maps Discord user IDs to their
current username and display name. Any other module can use this collection
as a lookup source in MongoDB aggregations:

    {"$lookup": {"from": "users", "localField": "user_id", "foreignField": "_id", "as": "user_info"}}

The collection schema is:
    { "_id": "<user_id>", "username": "<username>", "display_name": "<display_name>" }

On startup, all guild members are fetched and bulk-upserted. After that,
member join / leave / update events keep the collection current.
"""

import os

import pymongo
import pymongo.errors
from interactions import Client, Extension, listen
from interactions.api.events import MemberAdd, MemberRemove, MemberUpdate

from src import logutil
from src.mongodb import mongo_manager

logger = logutil.init_logger(os.path.basename(__file__))


class UserInfoExtension(Extension):
    """Keeps a per-guild `users` collection up to date with Discord display names."""

    def __init__(self, bot: Client):
        self.bot: Client = bot

    @listen()
    async def on_startup(self):
        """Fetch every member of every guild and bulk-upsert into MongoDB."""
        for guild in self.bot.guilds:
            guild_id = str(guild.id)
            collection = mongo_manager.get_guild_collection(guild_id, "users")

            members = guild.members  # already cached after gateway READY
            if not members:
                continue

            ops = []
            for member in members:
                if member.bot:
                    continue
                ops.append(
                    pymongo.UpdateOne(
                        {"_id": str(member.id)},
                        {"$set": {
                            "username": member.username,
                            "display_name": member.display_name,
                        }},
                        upsert=True,
                    )
                )

            if ops:
                try:
                    await collection.bulk_write(ops, ordered=False)
                    logger.info("Synced %d users for guild %s", len(ops), guild_id)
                except pymongo.errors.PyMongoError as e:
                    logger.warning("Failed to bulk-sync users for guild %s: %s", guild_id, e)

    @listen()
    async def on_member_add(self, event: MemberAdd):
        member = event.member
        if member.bot:
            return

        guild_id = str(event.guild_id)
        user_id = str(member.id)

        try:
            await mongo_manager.get_guild_collection(guild_id, "users").update_one(
                {"_id": user_id},
                {"$set": {
                    "username": member.username,
                    "display_name": member.display_name,
                }},
                upsert=True,
            )
        except pymongo.errors.PyMongoError as e:
            logger.warning("Failed to upsert user info for %s in guild %s: %s", user_id, guild_id, e)

    @listen()
    async def on_member_remove(self, event: MemberRemove):
        member = event.member
        if member.bot:
            return

        guild_id = str(event.guild_id)
        user_id = str(member.id)

        try:
            await mongo_manager.get_guild_collection(guild_id, "users").delete_one(
                {"_id": user_id},
            )
        except pymongo.errors.PyMongoError as e:
            logger.warning("Failed to remove user info for %s in guild %s: %s", user_id, guild_id, e)

    @listen()
    async def on_member_update(self, event: MemberUpdate):
        member = event.after
        if member.bot:
            return

        guild_id = str(event.guild_id)
        user_id = str(member.id)

        try:
            await mongo_manager.get_guild_collection(guild_id, "users").update_one(
                {"_id": user_id},
                {"$set": {
                    "username": member.username,
                    "display_name": member.display_name,
                }},
                upsert=True,
            )
        except pymongo.errors.PyMongoError as e:
            logger.warning("Failed to update user info for %s in guild %s: %s", user_id, guild_id, e)
