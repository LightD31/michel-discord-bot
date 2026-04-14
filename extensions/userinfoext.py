"""
User Info Extension

Maintains a per-guild `users` collection that maps Discord user IDs to their
current username and display name. Any other module can use this collection
as a lookup source in MongoDB aggregations:

    {"$lookup": {"from": "users", "localField": "user_id", "foreignField": "_id", "as": "user_info"}}

The collection schema is:
    { "_id": "<user_id>", "username": "<username>", "display_name": "<display_name>" }

Updates are triggered on every message so display names stay reasonably fresh.
"""

import os

import pymongo
from interactions import Client, Extension, Message, listen
from interactions.api.events import MessageCreate

from src import logutil
from src.helpers import is_guild_enabled
from src.mongodb import mongo_manager

logger = logutil.init_logger(os.path.basename(__file__))

# Collect user info for all guilds the bot is active in.
# Since this is a cross-module utility, no module-specific config is needed.


class UserInfoExtension(Extension):
    """Keeps a per-guild `users` collection up to date with Discord display names."""

    def __init__(self, bot: Client):
        self.bot: Client = bot

    @listen()
    async def on_message(self, event: MessageCreate):
        message: Message = event.message

        if message.guild is None:
            return
        if message.author.bot:
            return

        guild_id = str(message.guild.id)
        user_id = str(message.author.id)

        try:
            await mongo_manager.get_guild_collection(guild_id, "users").update_one(
                {"_id": user_id},
                {"$set": {
                    "username": message.author.username,
                    "display_name": message.author.display_name,
                }},
                upsert=True,
            )
        except pymongo.errors.PyMongoError as e:
            logger.warning("Failed to upsert user info for %s in guild %s: %s", user_id, guild_id, e)
