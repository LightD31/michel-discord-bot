"""LevelingMixin — message listener that awards XP and handles level-up notices."""

import random

import pymongo
from interactions import Client, Message, listen
from interactions.api.events import MessageCreate

from features.xp import (
    DEFAULT_LEVEL_UP_MESSAGE,
    XP_COOLDOWN_SECONDS,
    XP_MAX,
    XP_MIN,
    TTLCache,
    XpRepository,
    calculate_level,
)
from src.core.text import pick_weighted_message
from src.discord_ext.autocomplete import is_guild_enabled

from ._common import enabled_servers, logger, module_config


class LevelingMixin:
    # Attributes assembled by XpExtension.__init__
    bot: Client
    _db_connected: bool
    _rank_cache: TTLCache
    _repos: dict[str, XpRepository]

    def _repo(self, guild_id: str) -> XpRepository:
        repo = self._repos.get(guild_id)
        if repo is None:
            repo = XpRepository(guild_id)
            self._repos[guild_id] = repo
        return repo

    @listen()
    async def on_message(self, event: MessageCreate):
        """Give XP to a user when they send a message."""
        message = event.message

        if not self._db_connected:
            return

        if not self._is_valid_xp_message(message):
            return

        user_id = str(message.author.id)
        guild_id = str(message.guild.id)

        await self._ensure_guild_collection(guild_id, message.guild.name)

        repo = self._repo(guild_id)
        try:
            stats = await repo.get_user(user_id)
        except pymongo.errors.PyMongoError as e:
            logger.error("Database error when fetching user stats: %s", e)
            return

        if stats is None:
            await self._create_new_user(guild_id, user_id, message.created_at.timestamp())
            return

        last_time = stats.get("time", 0)
        if self._is_on_cooldown(message.created_at.timestamp(), last_time):
            logger.debug("No XP given to %s due to cooldown.", user_id)
            return

        await self._update_user_xp(guild_id, user_id, stats, message)

    def _is_valid_xp_message(self, message: Message) -> bool:
        if message.guild is None:
            return False
        if message.author.bot:
            logger.debug("Message was from a bot.")
            return False
        if not is_guild_enabled(message.guild.id, enabled_servers):
            logger.debug("Message was not from a guild with XP enabled.")
            return False
        return True

    async def _ensure_guild_collection(self, guild_id: str, guild_name: str) -> None:
        try:
            created = await self._repo(guild_id).ensure_collection(guild_name)
            if created:
                logger.debug("Created xp collection for %s.", guild_name)
        except pymongo.errors.PyMongoError as e:
            logger.error("Failed to create collection for %s: %s", guild_name, e)

    async def _create_new_user(self, guild_id: str, user_id: str, timestamp: float) -> bool:
        repo = self._repo(guild_id)
        try:
            await repo.insert_new_user(user_id, random.randint(XP_MIN, XP_MAX), timestamp)
            self._invalidate_rank_cache(guild_id)
            logger.debug("Added %s to the database.", user_id)
            return True
        except pymongo.errors.DuplicateKeyError:
            logger.debug("User %s already exists (race condition).", user_id)
            return True
        except pymongo.errors.PyMongoError as e:
            logger.error("Failed to create user %s: %s", user_id, e)
            return False

    def _invalidate_rank_cache(self, guild_id: str) -> None:
        for key in self._rank_cache.keys_with_prefix(f"{guild_id}_"):
            self._rank_cache.delete(key)

    @staticmethod
    def _is_on_cooldown(current_time: float, last_time: float) -> bool:
        return current_time - last_time < XP_COOLDOWN_SECONDS

    async def _update_user_xp(
        self, guild_id: str, user_id: str, stats: dict, message: Message
    ) -> None:
        xp_gained = random.randint(XP_MIN, XP_MAX)
        new_xp = stats.get("xp", 0) + xp_gained
        new_msg = stats.get("msg", 0) + 1
        repo = self._repo(guild_id)

        try:
            await repo.update_xp(user_id, new_xp, new_msg, message.created_at.timestamp())
            self._invalidate_rank_cache(guild_id)
            logger.debug("Gave %s XP.", user_id)
        except pymongo.errors.PyMongoError as e:
            logger.error("Failed to update XP for %s: %s", user_id, e)
            return

        try:
            await repo.log_event(user_id, xp_gained, new_xp, message.created_at)
        except pymongo.errors.PyMongoError as e:
            logger.warning("Failed to log XP event for %s: %s", user_id, e)

        new_level, _, _ = calculate_level(new_xp)
        old_level = stats.get("lvl", 0)
        if new_level > old_level:
            await self._handle_level_up(guild_id, user_id, new_level, message)

    async def _handle_level_up(
        self, guild_id: str, user_id: str, new_level: int, message: Message
    ) -> None:
        await self._repo(guild_id).set_level(user_id, new_level)
        logger.debug("%s is now level %d.", user_id, new_level)

        guild_config = module_config.get(guild_id, {})
        formatted_message = pick_weighted_message(
            guild_config,
            "levelUpMessageList",
            "levelUpMessageWeights",
            DEFAULT_LEVEL_UP_MESSAGE,
            mention=message.author.mention,
            lvl=new_level,
        )
        await message.channel.send(formatted_message)
