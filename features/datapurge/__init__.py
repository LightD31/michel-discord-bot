"""When a guild's data is deleted after the bot leaves it."""

from features.datapurge.repository import PURGE_DELAY, GuildPurgeRepository

__all__ = ["PURGE_DELAY", "GuildPurgeRepository"]
