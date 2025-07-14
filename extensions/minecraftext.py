"""
This module provides functionality for interacting with a Minecraft server using RCON.
"""

import asyncio
import os
from io import BytesIO, StringIO
from datetime import datetime, timedelta
import socket
from interactions import (
    Extension,
    listen,
    Task,
    IntervalTrigger,
    Embed,
    Timestamp,
    BrandColors,
    TimestampStyles,
    File,
    TimeTrigger,
    OrTrigger,
    BaseChannel,
    Message,
)
import pandas as pd
import prettytable
from mcstatus import JavaServer
from src import logutil
from src.minecraft_rcon import get_all_player_stats_rcon, get_server_info_rcon
from src.utils import create_dynamic_image, load_config

# Import necessary libraries and modules
logger = logutil.init_logger(os.path.basename(__file__))

# Get environment variables

config, module_config, enabled_servers = load_config("moduleMinecraft")
module_config = module_config[enabled_servers[0]]
MINECRAFT_ADDRESS = module_config["minecraftUrl"]
MINECRAFT_IP = module_config["minecraftIp"]
MINECRAFT_PORT = int(module_config["minecraftPort"])
CHANNEL_ID_KUBZ = module_config["minecraftChannelId"]
MESSAGE_ID_KUBZ = module_config["minecraftMessageId"]
RCON_HOST = module_config.get("minecraftRconHost", MINECRAFT_IP)
RCON_PORT = int(module_config.get("minecraftRconPort", 25575))
RCON_PASSWORD = module_config["minecraftRconPassword"]




# Define Minecraft extension class
class Minecraft(Extension):
    def __init__(self, client):
        self.client = client
        self.image_cache = {}
        self.serverColoc = None
        self.channel_edit_timestamp = datetime.fromtimestamp(0)

    # Start the status and stats tasks on bot startup
    @listen()
    async def on_startup(self):
        self.status.start()
        self.stats.start()
        # await self.stats()  # Commenté pour éviter l'exécution immédiate

    # Define Minecraft server object

    # Define status task to update Minecraft server status every 30 seconds
    @Task.create(IntervalTrigger(seconds=30))
    async def status(self):
        """
        Update the Minecraft server status and edit the status message in the designated Discord channel.
        """
        try:
            self.serverColoc = JavaServer.lookup(MINECRAFT_ADDRESS)
        except Exception:
            logger.info(
                "Could not find Minecraft server at %s using lookup", MINECRAFT_ADDRESS
            )
            self.serverColoc = JavaServer(MINECRAFT_IP, MINECRAFT_PORT)
        logger.debug("Updating Minecraft server status")
        channel: BaseChannel = await self.bot.fetch_channel(CHANNEL_ID_KUBZ)
        message : Message = await channel.fetch_message(MESSAGE_ID_KUBZ)
        try:
            embed2Timestamp = message.embeds[1].timestamp
        except IndexError:
            embed2Timestamp = Timestamp.utcnow()
        embed2 = Embed(
            title="Stats",
            description=f"Actualisé toutes les heures\nDernière actualisation : {embed2Timestamp.format(TimestampStyles.RelativeTime)}",
            images=("attachment://stats.png"),
            color=BrandColors.BLURPLE,
            timestamp=embed2Timestamp,
        )
        try:
            # Get Minecraft server status
            colocStatus = self.serverColoc.status()
            # If there are players online, get their names and display them in the status message
            if colocStatus.players.online > 0:
                players = "\n".join(
                    sorted(
                        [player.name for player in colocStatus.players.sample],
                        key=str.lower,
                    )
                )
                joueurs = f"Joueur{'s' if colocStatus.players.online > 1 else ''} ({colocStatus.players.online}/{colocStatus.players.max})"
            else:
                players = "\u200b"
                joueurs = "\u200b"
            # Create and format the status message
            embed1 = Embed(
                title=f"Serveur Forge {colocStatus.version.name}",
                description=f"Adresse : `{MINECRAFT_ADDRESS}`\nModpack : [Menagerie - a zoo modpack](https://www.curseforge.com/minecraft/modpacks/menagerie)\nVersion : **2.1.4**",
                fields=[
                    {
                        "name": "Latence",
                        "value": "{:.2f} ms".format(colocStatus.latency),
                        "inline": True,
                    },
                    {
                        "name": joueurs,
                        "value": players,
                        "inline": True,
                    },
                    {
                        "name": "État de Michel et du serveur",
                        "value": "https://status.drndvs.fr/status/coloc",
                    },
                ],
                color=BrandColors.GREEN,
                timestamp=Timestamp.utcnow().isoformat(),
            )
            # Edit the status message in the designated Discord channel
            await message.edit(content="", embeds=[embed1, embed2])
            # Modify the channel name if the number of players has changed
            name = f"🟢︱{colocStatus.players.online if colocStatus.players.online != 0 else 'aucun'}᲼joueur{'s' if colocStatus.players.online > 1 else ''}"
        # If the Minecraft server is offline, display an error message in the status message
        except (ConnectionResetError, ConnectionRefusedError, TimeoutError, socket.timeout) as e:
            logger.debug(e)
            embed1 = Embed(
                title="Serveur Hors-ligne",
                description=f"Adresse : `{MINECRAFT_ADDRESS}`",
                fields=[
                    {
                        "name": "État de Michel et du serveur",
                        "value": "https://status.drndvs.fr/status/coloc",
                    }
                ],
                color=BrandColors.RED,
                timestamp=Timestamp.utcnow().isoformat(),
            )
            await message.edit(content="", embeds=[embed1, embed2])
            # Modify the channel name if the server is offline
            name = "🔴︱hors-ligne"
        except (BrokenPipeError):
            # Create and format the status message
            try:
                title = message.embeds[0].title
            except IndexError:
                title = "Serveur Minecraft"
            embed1 = Embed(
                title=title,
                description=f"Adresse : `{MINECRAFT_ADDRESS}`\n",
                fields=[
                    {
                        "name": "Latence",
                        "value": "Serveur en veille :sleeping:",
                    },
                    {
                        "name" : "État de Michel et du serveur",
                        "value": "https://status.drndvs.fr/status/coloc"
                    }
                ],
                footer="Serveur Minecraft du believe",
                timestamp=Timestamp.utcnow().isoformat(),
                color=BrandColors.YELLOW,
            )
            await message.edit(content="", embeds=[embed1, embed2])
            name = "🟡︱veille"
        if (
            channel.name != name
            and self.channel_edit_timestamp < datetime.now() - timedelta(minutes=5)
        ):
            await channel.edit(name=name)
            self.channel_edit_timestamp = datetime.now()

    @Task.create(OrTrigger(*[TimeTrigger(hour=i, minute=10) for i in range(24)]))
    async def stats(self):
        logger.debug("Updating Minecraft server stats via RCON")
        channel = await self.bot.fetch_channel(CHANNEL_ID_KUBZ)
        message = await channel.fetch_message(MESSAGE_ID_KUBZ)
        embed1 = message.embeds[0]

        try:
            # Récupérer les statistiques via RCON
            player_stats_list = await get_all_player_stats_rcon(RCON_HOST, RCON_PORT, RCON_PASSWORD)
            
            if not player_stats_list:
                logger.info("Aucune statistique de joueur récupérée via RCON")
                # Garder l'ancien embed2 si pas de nouvelles données
                try:
                    embed2 = message.embeds[1]
                except IndexError:
                    embed2 = Embed(
                        title="Stats",
                        description="Aucune donnée disponible",
                        color=BrandColors.BLURPLE,
                        timestamp=Timestamp.utcnow().isoformat(),
                    )
                await message.edit(content="", embeds=[embed1, embed2])
                return

            # Convertir en DataFrame pour le traitement
            df = pd.DataFrame(player_stats_list)
            
            # Convertir le temps de jeu en format timedelta pour l'affichage
            df["Temps de jeu"] = pd.to_timedelta(df["Temps de jeu"], unit="s").dt.round("1s")
            df.sort_values(by="Temps de jeu", ascending=False, inplace=True)

            # Convertir le dataframe en CSV puis en prettytable
            output = StringIO()
            df.to_csv(output, index=False, float_format="%.2f")
            output.seek(0)

            table = prettytable.from_csv(output)
            table.align = "r"
            table.align["Joueur"] = "l"
            table.set_style(prettytable.SINGLE_BORDER)
            table.padding_width = 1
            table.title = "Statistiques des joueurs (RCON)"
            table.hrules = prettytable.ALL

            # Créer l'embed avec les statistiques
            embed2 = Embed(
                title="Stats",
                description=f"Actualisé toutes les heures à Xh10 via RCON\nDernière actualisation : {Timestamp.utcnow().format(TimestampStyles.RelativeTime)}",
                images=("attachment://stats.png"),
                color=BrandColors.BLURPLE,
                timestamp=Timestamp.utcnow().isoformat(),
            )

            # Vérifier le cache d'images
            table_string = table.get_string()
            if table_string in self.image_cache:
                await message.edit(content="", embeds=[embed1, embed2])
                logger.debug("Image from cache")
            else:
                # Créer une nouvelle image
                imageIO = BytesIO()
                image, imageIO = create_dynamic_image(table_string)
                self.image_cache = {}
                self.image_cache[table_string] = (image, imageIO)
                image_file = File(create_dynamic_image(table_string)[1], "stats.png")
                await message.edit(content="", embeds=[embed1, embed2], file=image_file)

        except Exception as e:
            logger.error(f"Erreur lors de la mise à jour des stats via RCON: {e}")
            # En cas d'erreur, garder l'ancien embed2
            try:
                embed2 = message.embeds[1]
            except IndexError:
                embed2 = Embed(
                    title="Stats",
                    description=f"Erreur lors de la récupération des données\nDernière tentative : {Timestamp.utcnow().format(TimestampStyles.RelativeTime)}",
                    color=BrandColors.RED,
                    timestamp=Timestamp.utcnow().isoformat(),
                )
            await message.edit(content="", embeds=[embed1, embed2])
