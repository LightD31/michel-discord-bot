"""StatsMixin — hourly stats task: the player table, rendered as an image."""

import asyncio

from interactions import (
    Embed,
    File,
    OrTrigger,
    Task,
    Timestamp,
    TimeTrigger,
)

from features.minecraft import MinecraftSettings
from features.minecraft import get_config as get_mc_config
from features.minecraft.table import build_stats_table
from src.core.images import create_dynamic_image
from src.discord_ext.messages import edit_message_if_changed

from ._common import MINECRAFT, build_stats_embed, logger


class StatsMixin:
    """Hourly player-stat collection and rendering."""

    @Task.create(OrTrigger(*[TimeTrigger(hour=i, minute=10) for i in range(24)]))
    async def stats(self):
        """Update each enabled guild's Minecraft player statistics every hour at X:10."""
        logger.debug("Updating Minecraft server stats")
        guilds = MINECRAFT.items()
        self._forget_disabled_guilds([guild_id for guild_id, _ in guilds])
        for guild_id, config in guilds:
            try:
                await self._update_stats(guild_id, config, MinecraftSettings.from_config(config))
            except Exception as e:  # noqa: BLE001 — one server down must not stop the others
                logger.error("Guild %s: failed to update Minecraft stats: %s", guild_id, e)

    async def _update_stats(self, guild_id: str, config: dict, settings: MinecraftSettings):
        message = await self.status_messages.get(guild_id, config)
        if message is None:
            logger.debug("Guild %s: no status message configured yet; skipping stats", guild_id)
            return
        embed1 = message.embeds[0] if message.embeds else Embed(title="Minecraft")

        player_stats = await self._get_player_stats(settings)
        table = build_stats_table(
            player_stats,
            max_players=get_mc_config("max_players_displayed", 15),
            name_length=get_mc_config("player_name_max_length", 14),
        )

        # Only move the "last refreshed" stamp, and render a new image, when the
        # table actually moved. Holding both otherwise is what lets the whole
        # payload compare equal, so an hour with no activity costs no edit at all.
        table_string = table.get_string() if table else None
        previous = message.embeds[1].timestamp if len(message.embeds) > 1 else None
        unchanged = table_string is not None and table_string == self._last_stats_tables.get(
            guild_id
        )
        self._last_stats_tables[guild_id] = table_string
        # Whole seconds only: the rendered label has second granularity anyway, and a
        # sub-second tail would just be one more thing to differ on round-trip.
        embed2 = build_stats_embed(
            previous if unchanged and previous else Timestamp.utcnow().replace(microsecond=0)
        )

        if table_string is None:
            logger.warning("Guild %s: no player statistics to display", guild_id)
        if table_string is None or unchanged:
            await edit_message_if_changed(
                message, content="", embeds=[embed1, embed2], logger=logger
            )
            return
        # Pillow work, off the event loop.
        _, image_io = await asyncio.to_thread(create_dynamic_image, table_string)
        await edit_message_if_changed(
            message,
            content="",
            embeds=[embed1, embed2],
            file=File(image_io, "stats.png"),
            logger=logger,
        )
        logger.debug("Guild %s: new stats image posted", guild_id)

    async def _get_player_stats(self, settings: MinecraftSettings):
        """Retrieve player statistics from the guild's Minecraft server."""
        from features.minecraft import get_minecraft_stats_with_retry

        if not settings.sftp_host_key:
            logger.error(
                "Minecraft stats skipped: no SFTP host key configured (minecraftSftpHostKey)"
            )
            return []
        try:
            logger.debug(
                "SFTP connection params: host=%s, port=%s, username=%s",
                settings.sftp_host,
                settings.sftp_port,
                settings.sftp_username,
            )
            player_stats = await get_minecraft_stats_with_retry(
                host=settings.sftp_host,
                port=settings.sftp_port,
                username=settings.sftp_username,
                password=settings.sftp_password,
                host_key=settings.sftp_host_key,
            )
            logger.debug(
                f"Retrieved stats for {len(player_stats)} players using optimized connection"
            )
            return player_stats

        except Exception as e:
            logger.error(
                "Failed to get stats with optimized method (host=%s, port=%s, user=%s): %s",
                settings.sftp_host,
                settings.sftp_port,
                settings.sftp_username,
                e,
            )
            return []
