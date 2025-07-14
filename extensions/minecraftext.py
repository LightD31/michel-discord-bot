"""
This module provides functionality for interacting with a Minecraft server.
"""

import asyncio
import os
import nbtlib
from io import BytesIO, StringIO
from datetime import datetime, timedelta
import asyncssh
import socket  # Ajoutez cette importation en haut du fichier
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
from interactions.client.utils import timestamp_converter
import pandas as pd
import prettytable
from mcstatus import JavaServer
from src import logutil
from src.minecraft import get_player_stats, get_users
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
SFTPS_PASSWORD = module_config["minecraftSftpsPassword"]




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
        # Nettoyer les caches au démarrage
        from src.minecraft import stats_cache
        stats_cache.clear()
        self.image_cache.clear()
        logger.info("Caches nettoyés au démarrage")
        
        self.status.start()
        self.stats.start()
        await self.stats()

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
        logger.debug("Updating Minecraft server stats")
        channel = await self.bot.fetch_channel(CHANNEL_ID_KUBZ)
        message = await channel.fetch_message(MESSAGE_ID_KUBZ)
        embed1 = message.embeds[0]

        # Connect to the Minecraft server using optimized SFTP connection
        from src.minecraft import get_minecraft_stats_with_retry
        
        try:
            player_stats = await get_minecraft_stats_with_retry(
                host="82.65.116.168",
                port=2225,
                username="Discord",
                password=SFTPS_PASSWORD
            )
            logger.debug(f"Retrieved stats for {len(player_stats)} players using optimized connection")
            
        except Exception as e:
            logger.error(f"Failed to get stats with optimized method: {e}")
            player_stats = []
                  
        # Convert the player stats to a pandas dataframe and format it (version optimisée)
        if player_stats:
            df = pd.DataFrame(player_stats)
            
            # Vérifier que la colonne "Temps de jeu" existe et est correcte
            if "Temps de jeu" in df.columns:
                df["Temps de jeu"] = pd.to_timedelta(df["Temps de jeu"], unit="s").dt.round("1s")
            
            # Trier par temps de jeu décroissant
            if "Temps de jeu" in df.columns:
                df.sort_values(by="Temps de jeu", ascending=False, inplace=True)
            
            # Limiter à 15 joueurs pour éviter que l'image soit trop grande
            df = df.head(15)
            
            # Utiliser la nouvelle fonction de formatage optimisée
            table = self.format_table_efficiently(df)
        else:
            # Créer une table vide
            df = pd.DataFrame(columns=["Joueur", "Niveau", "Morts", "Morts/h", "Marche (km)", "Temps de jeu"])
            table = self.format_table_efficiently(df)
            logger.warning("Aucune donnée de joueur récupérée")

        # Create an embed with the server stats and send it to the Discord channel
        embed2 = Embed(
            title="Stats",
            description=f"Actualisé toutes les heures à Xh10\nProchaine actualisation : {timestamp_converter(str(self.stats.next_run)).format(TimestampStyles.RelativeTime)}",
            images=("attachment://stats.png"),
            color=BrandColors.BLURPLE,
            timestamp=Timestamp.utcnow().isoformat(),
        )

        if table and table.get_string() in self.image_cache:
            await message.edit(content="", embeds=[embed1, embed2])
            logger.debug("Image récupérée depuis le cache")
        elif table:
            # Nettoyer le cache avant d'ajouter une nouvelle image
            self.optimize_image_cache()
            
            imageIO = BytesIO()
            image, imageIO = create_dynamic_image(table.get_string())
            self.image_cache[table.get_string()] = (image, imageIO)
            image = File(create_dynamic_image(table.get_string())[1], "stats.png")
            await message.edit(content="", embeds=[embed1, embed2], file=image)
            logger.debug("Nouvelle image générée et mise en cache")
        else:
            # Aucune table à afficher
            await message.edit(content="", embeds=[embed1, embed2])
            logger.warning("Aucune table de statistiques à afficher")

    def optimize_image_cache(self):
        """Nettoie le cache d'images pour éviter l'accumulation"""
        if len(self.image_cache) > 5:  # Garder seulement les 5 dernières images
            # Supprimer les plus anciennes (simple FIFO)
            oldest_keys = list(self.image_cache.keys())[:-5]
            for key in oldest_keys:
                del self.image_cache[key]
            logger.debug(f"Cache d'images nettoyé, {len(oldest_keys)} entrées supprimées")

    def format_table_efficiently(self, df):
        """Formate la table de manière efficace pour réduire la taille de l'image"""
        if df.empty:
            return None
            
        # Formatter les colonnes numériques pour réduire la largeur
        if "Morts/h" in df.columns:
            df["Morts/h"] = df["Morts/h"].round(2)
        if "Marche (km)" in df.columns:
            df["Marche (km)"] = df["Marche (km)"].round(1)
        if "Niveau" in df.columns:
            df["Niveau"] = df["Niveau"].astype(str)
            
        # Tronquer les noms trop longs
        if "Joueur" in df.columns:
            df["Joueur"] = df["Joueur"].str[:14]  # Limiter à 14 caractères
        
        # Convertir en table
        output = StringIO()
        df.to_csv(output, index=False, float_format="%.1f")
        output.seek(0)

        table = prettytable.from_csv(output)
        table.align = "r"
        table.align["Joueur"] = "l"
        table.set_style(prettytable.SINGLE_BORDER)
        table.padding_width = 1
        table.title = "Stats Joueurs (Top 15)"
        table.hrules = prettytable.ALL
        
        return table
