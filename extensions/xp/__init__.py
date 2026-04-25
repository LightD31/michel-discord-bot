"""XP Discord extension — message-based leveling and per-guild leaderboards.

Assembled as a mixin composition mirroring the minecraft/zunivers packages:
- :mod:`._common` — Pydantic config schema (``moduleXp``), logger, theme
- :mod:`.leveling` — :class:`.leveling.LevelingMixin`: ``on_message`` listener,
  cooldown and level-up handling
- :mod:`.commands` — :class:`.commands.CommandsMixin`: ``/rank``, ``/leaderboard``
- :mod:`.leaderboard` — :class:`.leaderboard.LeaderboardMixin`: permanent
  leaderboard updater task and embed builders

Persistence lives in :class:`features.xp.XpRepository` (MongoDB, one DB per
guild, collections ``xp`` and ``xp_events``).
"""

from interactions import Client, Extension, Task, TimeTrigger, listen

from features.xp import RANK_CACHE_TTL, USER_CACHE_TTL, TTLCache, XpRepository
from src.core.db import mongo_manager

from ._common import XpConfig, enabled_servers, logger, module_config
from .commands import CommandsMixin
from .leaderboard import LeaderboardMixin
from .leveling import LevelingMixin
from .voice import VoiceMixin


class XpExtension(Extension, LevelingMixin, VoiceMixin, CommandsMixin, LeaderboardMixin):
    """XP and leveling system extension."""

    def __init__(self, bot: Client):
        self.bot: Client = bot
        self._db_connected = True
        self._user_cache = TTLCache(ttl=USER_CACHE_TTL)
        self._rank_cache = TTLCache(ttl=RANK_CACHE_TTL)
        self._repos: dict[str, XpRepository] = {}
        self._voice_sessions: dict[tuple[str, str], float] = {}

        self._validate_config()
        logger.debug("enabled_servers for XP module: %s", enabled_servers)

    def _validate_config(self) -> None:
        for guild_id in enabled_servers:
            guild_config = module_config.get(guild_id, {})
            if not guild_config:
                logger.warning("No configuration found for guild %s", guild_id)
                continue
            if "xpChannelId" not in guild_config:
                logger.debug("No permanent leaderboard channel configured for guild %s", guild_id)
            if "levelUpMessageList" not in guild_config:
                logger.debug("Using default level up message for guild %s", guild_id)

    @listen()
    async def on_startup(self):
        try:
            connected = await mongo_manager.ping()
            if not connected:
                logger.error("MongoDB connection failed at startup, XP module disabled.")
                self._db_connected = False
        except Exception as e:
            logger.error("MongoDB ping failed: %s", e)
            self._db_connected = False

        for guild_id in enabled_servers:
            try:
                await self._repo(guild_id).ensure_indexes()
                logger.debug("Ensured indexes for guild %s", guild_id)
            except Exception as e:
                logger.error("Failed to create indexes for guild %s: %s", guild_id, e)

        self._cache_cleanup_task.start()
        self._voice_xp_tick.start()

        self.leaderboardpermanent.start()
        await self.leaderboardpermanent()

    @Task.create(TimeTrigger(minute=30, utc=False))
    async def _cache_cleanup_task(self):
        self._user_cache.cleanup()
        self._rank_cache.cleanup()
        logger.debug("Cache cleanup completed")


def setup(bot: Client) -> None:
    XpExtension(bot)


__all__ = ["XpConfig", "XpExtension", "setup"]
