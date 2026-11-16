"""Delete a guild's data 30 days after the bot leaves it.

The bot keeps a database per guild (``guild_<id>``) and its settings in
``config.json`` (``servers.<id>``). Nothing used to remove them when the bot
was kicked or the guild deleted, so they stayed for good, against what
``Privacy.md`` promises. Now:

* leaving a guild schedules its purge (``global.guild_purges``), and a guild
  the bot has data for but isn't in at startup (left while the bot was down)
  is scheduled then;
* a guild seen again (rejoined, or back after an outage) cancels it;
* a daily task deletes the database and the guild's settings once due
  (:data:`features.datapurge.PURGE_DELAY`).
"""

import os

from interactions import Client, Extension, Task, TimeTrigger, listen
from interactions.api.events import GuildAvailable, GuildJoin, GuildLeft

from features.datapurge import GuildPurgeRepository
from src.core import clock
from src.core import logging as logutil
from src.core.config import config_store
from src.core.db import mongo_manager
from src.core.errors import DatabaseError

logger = logutil.init_logger(os.path.basename(__file__))


def present_guild_ids(bot) -> set[str]:
    """Every guild the bot is in, unavailable ones included (READY lists them all)."""
    ids = getattr(getattr(bot, "user", None), "_guild_ids", None)
    if ids:
        return {str(gid) for gid in ids}
    return {str(g.id) for g in getattr(bot, "guilds", []) or []}


def drop_guild_settings(guild_id: str) -> None:
    """Remove ``servers.<guild_id>`` from ``config.json``."""

    def mutator(data: dict) -> None:
        (data.get("servers") or {}).pop(str(guild_id), None)

    config_store.mutate(mutator)


class DataPurgeExtension(Extension):
    """Schedules, cancels and runs the purge of the guilds the bot left."""

    MODULE_KEYS = ()

    def __init__(self, bot: Client):
        self.bot: Client = bot
        self.repo = GuildPurgeRepository()

    @listen()
    async def on_startup(self) -> None:
        present = present_guild_ids(self.bot)
        try:
            await self.repo.cancel(sorted(present))
            stored = await mongo_manager.guild_ids_with_data()
            for guild_id in sorted(set(stored) - present):
                await self.repo.schedule(guild_id, clock.now())
                logger.info("Guild %s: the bot isn't in it, its data is due for deletion", guild_id)
        except DatabaseError as e:
            logger.warning("Guild data purges not checked at startup: %s", e)
        if not self.purge_left_guilds.running:
            self.purge_left_guilds.start()

    @listen(GuildLeft)
    async def on_guild_left(self, event: GuildLeft) -> None:
        guild_id = str(event.guild_id)
        await self.repo.schedule(guild_id, clock.now())
        logger.info("Left guild %s: its data will be deleted in 30 days", guild_id)

    @listen(GuildJoin)
    async def on_guild_join(self, event: GuildJoin) -> None:
        if await self.repo.cancel([str(event.guild_id)]):
            logger.info("Back in guild %s: its data is kept", event.guild_id)

    @listen(GuildAvailable)
    async def on_guild_available(self, event: GuildAvailable) -> None:
        await self.repo.cancel([str(event.guild_id)])

    @Task.create(TimeTrigger(hour=4, minute=41, utc=False))
    async def purge_left_guilds(self) -> None:
        """Daily: delete the data of the guilds left more than 30 days ago."""
        present = present_guild_ids(self.bot)
        for guild_id in await self.repo.due(clock.now()):
            if guild_id in present:  # back without an event we saw
                await self.repo.cancel([guild_id])
                continue
            await self.repo.purge(guild_id)
            drop_guild_settings(guild_id)
            logger.info("Guild %s: data and settings deleted, 30 days after leaving", guild_id)


def setup(bot: Client) -> None:
    DataPurgeExtension(bot)
