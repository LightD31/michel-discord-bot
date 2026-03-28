"""
This module provides functionality for interacting with a Minecraft server.
"""

import os
import socket
from datetime import datetime, timedelta
from io import BytesIO, StringIO

import pandas as pd
import prettytable
from interactions import (
    BaseChannel,
    BrandColors,
    Embed,
    Extension,
    File,
    Guild,
    IntervalTrigger,
    Message,
    OrTrigger,
    ScheduledEventStatus,
    ScheduledEventType,
    Task,
    TimeTrigger,
    Timestamp,
    TimestampStyles,
    listen,
)
from interactions.client.utils import timestamp_converter
from mcstatus import JavaServer

from src import logutil
from src.minecraft_config import get_config as get_mc_config
from src.utils import create_dynamic_image, load_config

# Initialize logger
logger = logutil.init_logger(os.path.basename(__file__))

# Load configuration
config, module_config, enabled_servers = load_config("moduleMinecraft")
module_config = module_config[enabled_servers[0]] if enabled_servers else {}

# Configuration constants
MINECRAFT_ADDRESS = module_config.get("minecraftUrl", "")
MINECRAFT_IP = module_config.get("minecraftIp", "")
MINECRAFT_PORT = int(module_config.get("minecraftPort", 0))
CHANNEL_ID_KUBZ = module_config.get("minecraftChannelId")
MESSAGE_ID_KUBZ = module_config.get("minecraftMessageId")
SFTPS_PASSWORD = module_config.get("minecraftSftpsPassword", "")
SFTP_HOST = module_config.get("minecraftSftpHost", MINECRAFT_IP)
SFTP_PORT = int(module_config.get("minecraftSftpPort", 2225))
SFTP_USERNAME = module_config.get("minecraftSftpUsername", "Discord")
MODPACK_NAME = module_config.get("minecraftModpackName", "")
MODPACK_URL = module_config.get("minecraftModpackUrl", "")
MODPACK_VERSION = module_config.get("minecraftModpackVersion", "")
STATUS_URL = module_config.get("minecraftStatusUrl", "")
FOOTER_TEXT = module_config.get("minecraftFooterText", "")
SERVER_TYPE = module_config.get("minecraftServerType", "")


class Minecraft(Extension):
    """Discord extension for Minecraft server monitoring and statistics."""
    
    def __init__(self, client):
        self.client = client
        self.image_cache = {}
        self.serverColoc = None
        self.channel_edit_timestamp = datetime.fromtimestamp(0)
        self.scheduled_event = None

    @listen()
    async def on_startup(self):
        """Initialize the extension on bot startup."""
        if not enabled_servers:
            logger.warning("moduleMinecraft is not enabled for any server, skipping startup")
            return
        # Clear caches on startup
        from src.minecraft import stats_cache
        stats_cache.clear()
        self.image_cache.clear()
        logger.info("Caches cleared on startup")

        # Recover existing scheduled event created by the bot
        try:
            channel = await self.bot.fetch_channel(CHANNEL_ID_KUBZ)
            guild = channel.guild
            for event in await guild.list_scheduled_events():
                creator = await event.creator
                if creator.id == self.bot.user.id and "Minecraft" in (event.name or ""):
                    self.scheduled_event = event
                    logger.info(f"Recovered existing Minecraft scheduled event: {event.name}")
                    break
        except Exception as e:
            logger.error(f"Failed to recover scheduled event: {e}")

        self.status.start()
        self.stats.start()
        await self.stats()

    @Task.create(IntervalTrigger(seconds=30))
    async def status(self):
        """Update the Minecraft server status every 30 seconds."""
        try:
            self.serverColoc = JavaServer.lookup(MINECRAFT_ADDRESS)
        except Exception:
            logger.info("Could not find Minecraft server at %s using lookup", MINECRAFT_ADDRESS)
            self.serverColoc = JavaServer(MINECRAFT_IP, MINECRAFT_PORT)
            
        logger.debug("Updating Minecraft server status")
        try:
            logger.debug(f"Fetching channel={CHANNEL_ID_KUBZ}, message={MESSAGE_ID_KUBZ}")
            channel: BaseChannel = await self.bot.fetch_channel(CHANNEL_ID_KUBZ)
            message: Message = await channel.fetch_message(MESSAGE_ID_KUBZ)

            try:
                embed2_timestamp = message.embeds[1].timestamp
            except IndexError:
                embed2_timestamp = Timestamp.utcnow()

            embed2 = Embed(
                title="Stats",
                description=f"Actualisé toutes les heures\nDernière actualisation : {embed2_timestamp.format(TimestampStyles.RelativeTime)}",
                images=("attachment://stats.png"),
                color=BrandColors.BLURPLE,
                timestamp=embed2_timestamp,
            )

            players_online = 0
            try:
                # Get Minecraft server status
                coloc_status = self.serverColoc.status()
                embed1, name = self._create_online_embed(coloc_status)
                players_online = coloc_status.players.online

            except (ConnectionResetError, ConnectionRefusedError, TimeoutError, socket.timeout) as e:
                logger.debug(e)
                embed1, name = self._create_offline_embed()

            except BrokenPipeError:
                embed1, name = self._create_sleeping_embed(message)

            await message.edit(content="", embeds=[embed1, embed2])
            await self._update_channel_name(channel, name)
            await self._update_scheduled_event(channel, players_online)
        except Exception as e:
            logger.error(f"Failed to update Minecraft server status: {e}")

    def _create_online_embed(self, coloc_status):
        """Create embed for online server status."""
        if coloc_status.players.online > 0:
            players = "\n".join(
                sorted(
                    [player.name.replace("_", r"\_") for player in coloc_status.players.sample],
                    key=str.lower,
                )
            )
            joueurs = f"Joueur{'s' if coloc_status.players.online > 1 else ''} ({coloc_status.players.online}/{coloc_status.players.max})"
        else:
            players = "\u200b"
            joueurs = "\u200b"
            
        embed = Embed(
            title=f"Serveur {SERVER_TYPE + ' ' if SERVER_TYPE else ''}{coloc_status.version.name}",
            description=f"Adresse : `{MINECRAFT_ADDRESS}`"
            + (f"\nModpack : [{MODPACK_NAME}]({MODPACK_URL})" if MODPACK_NAME and MODPACK_URL else "")
            + (f"\nVersion : **{MODPACK_VERSION}**" if MODPACK_VERSION else ""),
            fields=[
                {
                    "name": "Latence",
                    "value": "{:.2f} ms".format(coloc_status.latency),
                    "inline": True,
                },
                {
                    "name": joueurs,
                    "value": players,
                    "inline": True,
                },
            ] + ([{
                    "name": "État de Michel et du serveur",
                    "value": STATUS_URL,
                }] if STATUS_URL else []),
            color=BrandColors.GREEN,
            timestamp=Timestamp.utcnow().isoformat(),
        )
        name = f"🟢︱{coloc_status.players.online if coloc_status.players.online != 0 else 'aucun'}᲼joueur{'s' if coloc_status.players.online > 1 else ''}"
        return embed, name

    def _create_offline_embed(self):
        """Create embed for offline server status."""
        embed = Embed(
            title="Serveur Hors-ligne",
            description=f"Adresse : `{MINECRAFT_ADDRESS}`",
            fields=[{
                    "name": "État de Michel et du serveur",
                    "value": STATUS_URL,
                }] if STATUS_URL else [],
            color=BrandColors.RED,
            timestamp=Timestamp.utcnow().isoformat(),
        )
        return embed, "🔴︱hors-ligne"

    def _create_sleeping_embed(self, message):
        """Create embed for sleeping server status."""
        try:
            title = message.embeds[0].title
        except IndexError:
            title = "Serveur Minecraft"
            
        embed = Embed(
            title=title,
            description=f"Adresse : `{MINECRAFT_ADDRESS}`\n",
            fields=[
                {
                    "name": "Latence",
                    "value": "Serveur en veille :sleeping:",
                },
            ] + ([{
                    "name": "État de Michel et du serveur",
                    "value": STATUS_URL,
                }] if STATUS_URL else []),
            footer=FOOTER_TEXT if FOOTER_TEXT else None,
            timestamp=Timestamp.utcnow().isoformat(),
            color=BrandColors.YELLOW,
        )
        return embed, "🟡︱veille"

    async def _update_channel_name(self, channel, name):
        """Update channel name if needed and not recently changed."""
        if (
            channel.name != name
            and self.channel_edit_timestamp < datetime.now() - timedelta(minutes=5)
        ):
            await channel.edit(name=name)
            self.channel_edit_timestamp = datetime.now()

    async def _update_scheduled_event(self, channel, players_online):
        """Create or delete a Discord scheduled event based on player count."""
        try:
            guild: Guild = channel.guild
            if players_online > 0:
                if not self.scheduled_event:
                    event_name = f"Minecraft - {players_online} joueur{'s' if players_online > 1 else ''} en ligne"
                    self.scheduled_event = await guild.create_scheduled_event(
                        name=event_name,
                        event_type=ScheduledEventType.EXTERNAL,
                        external_location=f"Serveur Minecraft : {MINECRAFT_ADDRESS}",
                        start_time=datetime.now().astimezone() + timedelta(seconds=5),
                        end_time=datetime.now().astimezone() + timedelta(days=1),
                        description=f"Des joueurs sont connectés sur le serveur Minecraft !\nAdresse : `{MINECRAFT_ADDRESS}`",
                    )
                    await self.scheduled_event.edit(status=ScheduledEventStatus.ACTIVE)
                    logger.info(f"Created Minecraft scheduled event: {event_name}")
                else:
                    event_name = f"Minecraft - {players_online} joueur{'s' if players_online > 1 else ''} en ligne"
                    if self.scheduled_event.name != event_name:
                        await self.scheduled_event.edit(
                            name=event_name,
                            end_time=datetime.now().astimezone() + timedelta(days=1),
                        )
                        logger.debug(f"Updated Minecraft scheduled event: {event_name}")
            else:
                if self.scheduled_event:
                    await self.scheduled_event.delete()
                    self.scheduled_event = None
                    logger.info("Deleted Minecraft scheduled event (no players online)")
        except Exception as e:
            logger.error(f"Failed to update scheduled event: {e}")
            self.scheduled_event = None

    @Task.create(OrTrigger(*[TimeTrigger(hour=i, minute=10) for i in range(24)]))
    async def stats(self):
        """Update Minecraft server statistics every hour at X:10."""
        logger.debug("Updating Minecraft server stats")
        channel = await self.bot.fetch_channel(CHANNEL_ID_KUBZ)
        message = await channel.fetch_message(MESSAGE_ID_KUBZ)
        embed1 = message.embeds[0]

        # Get player statistics using optimized SFTP connection
        player_stats = await self._get_player_stats()
                  
        # Convert player stats to formatted table
        table = self._create_stats_table(player_stats)

        # Create stats embed
        embed2 = Embed(
            title="Stats",
            description=f"Actualisé toutes les heures à Xh10\nProchaine actualisation : {timestamp_converter(str(self.stats.next_run)).format(TimestampStyles.RelativeTime)}",
            images=("attachment://stats.png"),
            color=BrandColors.BLURPLE,
            timestamp=Timestamp.utcnow().isoformat(),
        )

        # Handle image caching and generation
        await self._update_stats_message(message, embed1, embed2, table)

    async def _get_player_stats(self):
        """Retrieve player statistics from the Minecraft server."""
        from src.minecraft import get_minecraft_stats_with_retry
        
        try:
            logger.debug(f"SFTP connection params: host={SFTP_HOST}, port={SFTP_PORT}, username={SFTP_USERNAME}")
            player_stats = await get_minecraft_stats_with_retry(
                host=SFTP_HOST,
                port=SFTP_PORT,
                username=SFTP_USERNAME,
                password=SFTPS_PASSWORD
            )
            logger.debug(f"Retrieved stats for {len(player_stats)} players using optimized connection")
            return player_stats

        except Exception as e:
            logger.error(f"Failed to get stats with optimized method (host={SFTP_HOST}, port={SFTP_PORT}, user={SFTP_USERNAME}): {e}")
            return []

    def _create_stats_table(self, player_stats):
        """Create and format the statistics table."""
        if not player_stats:
            # Create empty table
            df = pd.DataFrame(columns=["Joueur", "Niveau", "Morts", "Morts/h", "Marche (km)", "Temps de jeu", "Blocs minés", "Mobs tués", "Animaux reproduits"])
            logger.warning("No player data retrieved")
        else:
            df = pd.DataFrame(player_stats)
            
            # Validate and format "Temps de jeu" column
            if "Temps de jeu" in df.columns:
                df["Temps de jeu"] = pd.to_timedelta(df["Temps de jeu"], unit="s").dt.round("1s")
                df.sort_values(by="Temps de jeu", ascending=False, inplace=True)
            
            # Limit to top N players to keep image size manageable
            df = df.head(get_mc_config("max_players_displayed", 15))
        
        return self._format_table_efficiently(df)

    async def _update_stats_message(self, message, embed1, embed2, table):
        """Update the stats message with caching logic."""
        if not table:
            await message.edit(content="", embeds=[embed1, embed2])
            logger.warning("No statistics table to display")
            return

        table_string = table.get_string()
        
        if table_string in self.image_cache:
            await message.edit(content="", embeds=[embed1, embed2])
            logger.debug("Image retrieved from cache")
        else:
            # Clean cache before adding new image
            self._optimize_image_cache()
            
            imageIO = BytesIO()
            image, imageIO = create_dynamic_image(table_string)
            self.image_cache[table_string] = (image, imageIO)
            image = File(create_dynamic_image(table_string)[1], "stats.png")
            await message.edit(content="", embeds=[embed1, embed2], file=image)
            logger.debug("New image generated and cached")

    def _optimize_image_cache(self):
        """Clean image cache to prevent memory accumulation."""
        if len(self.image_cache) > get_mc_config("max_image_cache_size", 5):
            # Remove oldest entries (simple FIFO)
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
            return f"{num/1000000:.1f}M"
        elif num >= 1000:
            return f"{num/1000:.1f}k"
        else:
            return str(int(num))

    def _format_table_efficiently(self, df):
        """Format table efficiently to reduce image size."""
        if df.empty:
            return None
            
        # Format numeric columns to reduce width
        if "Morts/h" in df.columns:
            df["Morts/h"] = df["Morts/h"].round(2)
        if "Marche (km)" in df.columns:
            df["Marche (km)"] = df["Marche (km)"].round(1)
        if "Niveau" in df.columns:
            df["Niveau"] = df["Niveau"].astype(str)
        
        # Format large numbers for better readability
        if "Blocs minés" in df.columns:
            df["Blocs minés"] = df["Blocs minés"].apply(self._format_large_number)
        if "Mobs tués" in df.columns:
            df["Mobs tués"] = df["Mobs tués"].apply(self._format_large_number)
        if "Animaux reproduits" in df.columns:
            df["Animaux reproduits"] = df["Animaux reproduits"].apply(self._format_large_number)
            
        # Truncate long names
        if "Joueur" in df.columns:
            df["Joueur"] = df["Joueur"].str[:get_mc_config("player_name_max_length", 14)]
        
        # Convert to table
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
