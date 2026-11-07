"""StatsMixin — hourly stats task, table rendering, and image caching."""

from io import BytesIO, StringIO

import pandas as pd
import prettytable
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
        table = self._create_stats_table(player_stats)

        # Only move the "last refreshed" stamp when the table actually moved.
        # Holding it otherwise is what lets the whole payload compare equal, so
        # an hour with no activity costs no edit at all.
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

        await self._update_stats_message(message, embed1, embed2, table)

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

    def _create_stats_table(self, player_stats):
        """Create and format the statistics table."""
        if not player_stats:
            df = pd.DataFrame(
                columns=[
                    "Joueur",
                    "Niveau",
                    "Morts",
                    "Morts/h",
                    "Marche (km)",
                    "Temps de jeu",
                    "Blocs minés",
                    "Mobs tués",
                    "Animaux reproduits",
                ]
            )
            logger.warning("No player data retrieved")
        else:
            df = pd.DataFrame(player_stats)

            if "Temps de jeu" in df.columns:
                df["Temps de jeu"] = pd.to_timedelta(df["Temps de jeu"], unit="s").dt.round("1s")
                df.sort_values(by="Temps de jeu", ascending=False, inplace=True)

            df = df.head(get_mc_config("max_players_displayed", 15))

        return self._format_table_efficiently(df)

    async def _update_stats_message(self, message, embed1, embed2, table):
        """Update the stats message with caching logic."""
        if not table:
            await edit_message_if_changed(
                message, content="", embeds=[embed1, embed2], logger=logger
            )
            logger.warning("No statistics table to display")
            return

        table_string = table.get_string()

        if table_string in self.image_cache:
            await edit_message_if_changed(
                message, content="", embeds=[embed1, embed2], logger=logger
            )
            logger.debug("Image retrieved from cache")
        else:
            self._optimize_image_cache()

            imageIO = BytesIO()
            image, imageIO = create_dynamic_image(table_string)
            self.image_cache[table_string] = (image, imageIO)
            image = File(create_dynamic_image(table_string)[1], "stats.png")
            await edit_message_if_changed(
                message, content="", embeds=[embed1, embed2], file=image, logger=logger
            )
            logger.debug("New image generated and cached")

    def _optimize_image_cache(self):
        """Clean image cache to prevent memory accumulation."""
        if len(self.image_cache) > get_mc_config("max_image_cache_size", 5):
            max_cache = get_mc_config("max_image_cache_size", 5)
            oldest_keys = list(self.image_cache.keys())[:-max_cache]
            for key in oldest_keys:
                del self.image_cache[key]
            logger.debug(f"Image cache cleaned, {len(oldest_keys)} entries removed")

    def _format_large_number(self, num):
        """Format large numbers for better readability."""
        if pd.isna(num):
            return "0"
        if num >= 1000000:
            return f"{num / 1000000:.1f}M"
        elif num >= 1000:
            return f"{num / 1000:.1f}k"
        else:
            return str(int(num))

    def _format_table_efficiently(self, df):
        """Format table efficiently to reduce image size."""
        if df.empty:
            return None

        if "Morts/h" in df.columns:
            df["Morts/h"] = df["Morts/h"].round(2)
        if "Marche (km)" in df.columns:
            df["Marche (km)"] = df["Marche (km)"].round(1)
        if "Niveau" in df.columns:
            df["Niveau"] = df["Niveau"].astype(str)

        if "Blocs minés" in df.columns:
            df["Blocs minés"] = df["Blocs minés"].apply(self._format_large_number)
        if "Mobs tués" in df.columns:
            df["Mobs tués"] = df["Mobs tués"].apply(self._format_large_number)
        if "Animaux reproduits" in df.columns:
            df["Animaux reproduits"] = df["Animaux reproduits"].apply(self._format_large_number)

        if "Joueur" in df.columns:
            df["Joueur"] = df["Joueur"].str[: get_mc_config("player_name_max_length", 14)]

        output = StringIO()
        df.to_csv(output, index=False, float_format="%.1f")
        output.seek(0)

        table = prettytable.from_csv(output)
        table.align = "r"
        table.align["Joueur"] = "l"
        table.set_style(prettytable.SINGLE_BORDER)
        table.padding_width = 1
        max_players = get_mc_config("max_players_displayed", 15)
        table.title = f"Stats Joueurs (Top {max_players})"
        table.hrules = prettytable.ALL

        return table
