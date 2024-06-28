"""
This module provides functionality for interacting with a Minecraft server.
"""

import asyncio
import os
import nbtlib
from io import BytesIO, StringIO
from datetime import datetime, timedelta
import asyncssh
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
        self.status.start()
        self.stats.start()
        # await self.stats()

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
                title=f"Serveur {colocStatus.version.name}",
                description=f"Adresse : `{MINECRAFT_ADDRESS}`\nModpack : [Cisco's Fantasy Medieval RPG Lite](https://www.curseforge.com/minecraft/modpacks/ciscos-fantasy-medieval-adventure-rpg)\nVersion : **15D**",
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
            # embed1.description = f"Adresse : `{MINECRAFT_ADDRESS}`\nCarte 2D : [Cliquez ici](https://pl3xmap-coloc.drndvs.fr 'Pl3xMap')\nCarte 3D : [Cliquez ici](https://bluemap-coloc.drndvs.fr 'BlueMap')\nStats : [Cliquez ici](http://stats-coloc.drndvs.fr/stats/index.html 'Stats')"
            # Edit the status message in the designated Discord channel
            await message.edit(content="", embeds=[embed1, embed2])
            # Modify the channel name if the number of players has changed
            name = f"🟢︱{colocStatus.players.online if colocStatus.players.online != 0 else 'aucun'}᲼joueur{'s' if colocStatus.players.online > 1 else ''}"
        # If the Minecraft server is offline, display an error message in the status message
        except (ConnectionResetError, ConnectionRefusedError) as e:
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

        # Connect to the Minecraft server using SSH and SFTP
        async with asyncssh.connect(
            host="192.168.0.126",
            port=2224,
            username="Discord",
            password=SFTPS_PASSWORD,
            known_hosts=None,
        ) as conn:
            async with conn.start_sftp_client() as sftp:
                tasks = []
                # async with sftp.open("world/data/pmmo.dat", 'rb') as f:
                #     # Save the file locally as pmmo.dat
                #     with open("data/pmmo.dat", "wb") as file:
                #         data = await f.read()
                #         file.write(data)
                # # Load the pmmo.dat file and get the player stats
                # nbtfile = nbtlib.load("data/pmmo.dat")
                # nbtfile = nbtfile['']
                tasks.append(get_users(sftp, "usercache.json"))
                files = await sftp.glob("world/stats/*json")
                for file in files:
                    logger.debug("Opening %s", file)
                    # Find corrsponding .dat file in world/playerdata
                    nbtfile = f"world/playerdata/{file.removeprefix('world/stats/').removesuffix('.json')}.dat"                    
                    tasks.append(get_player_stats(sftp, file, nbtfile))
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
        table.title = "Statistiques des joueurs"
        table.hrules = prettytable.ALL

        # Create an embed with the server stats and send it to the Discord channel
        embed2 = Embed(
            title="Stats",
            description=f"Actualisé toutes les heures à Xh10\nDernière actualisation : {Timestamp.utcnow().format(TimestampStyles.RelativeTime)}",
            images=("attachment://stats.png"),
            color=BrandColors.BLURPLE,
            timestamp=Timestamp.utcnow().isoformat(),
        )

        if table.get_string() in self.image_cache:
            await message.edit(content="", embeds=[embed1, embed2])
            logger.debug("Image from cache")
        else:
            imageIO = BytesIO()
            image, imageIO = create_dynamic_image(table.get_string())
            self.image_cache = {}
            self.image_cache[table.get_string()] = (image, imageIO)
            image = File(create_dynamic_image(table.get_string())[1], "stats.png")
            await message.edit(content="", embeds=[embed1, embed2], file=image)
