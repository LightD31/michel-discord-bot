"""
This module provides functionality for interacting with a Minecraft server.
"""

import asyncio
import os
from io import BytesIO, StringIO

import asyncssh
import interactions
import pandas as pd
import prettytable
from dotenv import load_dotenv
from mcstatus import JavaServer

from src import logutil
from src.minecraft import get_player_stats, get_users
from src.utils import create_dynamic_image

# Import necessary libraries and modules
logger = logutil.init_logger(os.path.basename(__file__))
load_dotenv()

# Get environment variables
MINECRAFT_ADDRESS = os.environ.get("MINECRAFT_ADDRESS")
CHANNEL_ID_KUBZ = int(os.environ.get("CHANNEL_ID_KUBZ"))
MESSAGE_ID_KUBZ = int(os.environ.get("MESSAGE_ID_KUBZ"))
SFTPS_PASSWORD = os.environ.get("SFTPS_PASSWORD")

# Define Minecraft extension class
class Minecraft(interactions.Extension):
    def __init__(self, client):
        self.client = client
        self.image_cache = {}


    # Start the status and stats tasks on bot startup
    @interactions.listen()
    async def on_startup(self):
        self.status.start()
        self.stats.start()

    # Define Minecraft server object
    serverColoc = JavaServer(MINECRAFT_ADDRESS, 25565)

    # Define status task to update Minecraft server status every 30 seconds
    @interactions.Task.create(interactions.IntervalTrigger(seconds=30))
    async def status(self, serverColoc=serverColoc):
        """
        Update the Minecraft server status and edit the status message in the designated Discord channel.
        """
        logger.debug("Updating Minecraft server status")
        channel = await self.bot.fetch_channel(CHANNEL_ID_KUBZ)
        message = await channel.fetch_message(MESSAGE_ID_KUBZ)
        embed2Timestamp = message.embeds[1].timestamp
        embed2 = interactions.Embed(
            title="Stats",
            description=f"Actualisé toutes les 5 minutes\nDernière actualisation : {embed2Timestamp.format(interactions.TimestampStyles.RelativeTime)}",
            images=("attachment://stats.png"),
            color=0x00AA00,
            timestamp=embed2Timestamp,
        )
        try:
            # Get Minecraft server status
            colocStatus = serverColoc.status()

            # If there are players online, get their names and display them in the status message
            if colocStatus.players.online > 0:
                players = "\n".join(
                    sorted([player.name for player in colocStatus.players.sample], key=str.lower)
                )
                joueurs = f"Joueur{'s' if colocStatus.players.online > 1 else ''} ({colocStatus.players.online}/{colocStatus.players.max})"
            else:
                players = "\u200b"
                joueurs = "\u200b"

            # Create and format the status message
            embed1 = interactions.Embed()
            embed1.description = f"Adresse : `{MINECRAFT_ADDRESS}:25565`\nCarte 2D : [Cliquez ici](https://pl3xmap-coloc.drndvs.fr 'Pl3xMap')\nCarte 3D : [Cliquez ici](https://bluemap-coloc.drndvs.fr 'BlueMap')\nStats : [Cliquez ici](http://stats-coloc.drndvs.fr/stats/index.html 'Stats')"
            embed1.add_fields(
                interactions.EmbedField(
                    "Latence", "{:.2f} ms".format(colocStatus.latency), True
                ),
                interactions.EmbedField(joueurs, players, True),
                interactions.EmbedField(
                    "Dernière actualisation (Toutes les 30 secondes)",
                    interactions.Timestamp.utcnow().format(
                        interactions.TimestampStyles.RelativeTime
                    ),
                ),
            )
            embed1.title = "Serveur " + str(colocStatus.version.name)
            embed1.set_footer("Serveur Minecraft du believe")
            embed1.timestamp = interactions.Timestamp.utcnow().isoformat()
            embed1.color = 0x00AA00

            # Edit the status message in the designated Discord channel
            await message.edit(content="", embeds=[embed1, embed2])

        # If the Minecraft server is offline, display an error message in the status message
        except (ConnectionResetError, ConnectionRefusedError) as e:
            logger.debug(e)
            embed1 = interactions.Embed(
                title="Serveur Hors-ligne",
                description="Adresse : `http://" + MINECRAFT_ADDRESS + ":25565`",
                fields=[
                    {
                        "name": "Dernière actualisation",
                        "value": interactions.Timestamp.utcnow().format(
                            interactions.TimestampStyles.RelativeTime
                        ),
                    }
                ],
                color=0xAA0000,
                timestamp=interactions.Timestamp.utcnow().isoformat(),
            )
            await message.edit(content="", embeds=[embed1, embed2])

    @interactions.Task.create(interactions.IntervalTrigger(minutes=5, seconds=10))
    async def stats(self):
        logger.debug("Updating Minecraft server stats")
        channel = await self.bot.fetch_channel(CHANNEL_ID_KUBZ)
        message = await channel.fetch_message(MESSAGE_ID_KUBZ)
        embed1 = message.embeds[0]
        
        # Connect to the Minecraft server using SSH and SFTP
        async with asyncssh.connect(
            host="192.168.0.13",
            port=2224,
            username="admin",
            password=SFTPS_PASSWORD,
            known_hosts=None,
        ) as conn:
            async with conn.start_sftp_client() as sftp:
                tasks = []
                tasks.append(get_users(sftp, "usercache.json"))
                files = await sftp.glob("world/stats/*json")
                for file in files:
                    logger.debug("Opening %s", file)
                    tasks.append(get_player_stats(sftp, file))
                results = await asyncio.gather(*tasks)
        
        # Convert the player stats to a pandas dataframe and format it
        users_dict = results[0]
        uuid_to_name_dict = {item["uuid"]: item["name"] for item in users_dict}
        df = pd.DataFrame(results[1:])
        df["Joueur"] = df["Joueur"].map(uuid_to_name_dict)
        df["Temps de jeu"] = pd.to_timedelta(df["Temps de jeu"], unit="s").dt.round(
            "1s"
        )
        df.sort_values(by="Temps de jeu", ascending=False, inplace=True)

        # Convert the dataframe to a prettytable and create an image of it
        output = StringIO()
        df.to_csv(output, index=False, float_format="%.2f")
        output.seek(0)

        table = prettytable.from_csv(output)
        table.align = "r"
        table.align["Joueur"] = "l"
        table.set_style(prettytable.SINGLE_BORDER)
        table.padding_width = 1

        # Create an embed with the server stats and send it to the Discord channel
        embed2 = interactions.Embed(
            title="Stats",
            description=f"Actualisé toutes les 5 minutes\nDernière actualisation : {interactions.Timestamp.utcnow().format(interactions.TimestampStyles.RelativeTime)}",
            images=("attachment://stats.png"),
            color=0x00AA00,
            timestamp=interactions.Timestamp.utcnow().isoformat(),
        )

        if table.get_string() in self.image_cache:
            await message.edit(content="", embeds=[embed1, embed2])
            logger.debug("Image from cache")
        else:
            imageIO = BytesIO()
            image, imageIO = create_dynamic_image(table.get_string())
            self.image_cache = {}
            self.image_cache[table.get_string()] = (image, imageIO)
            image = interactions.File(
                create_dynamic_image(table.get_string())[1], "stats.png"
            )
            await message.edit(content="", embeds=[embed1, embed2], file=image)
