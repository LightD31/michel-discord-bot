import os
from datetime import datetime
from typing import Optional

from interactions import (
    BaseChannel,
    Embed,
    Extension,
    IntervalTrigger,
    Message,
    Task,
    listen,
)
from pyfactorybridge import API

from src import logutil
from src.utils import load_config

logger = logutil.init_logger(os.path.basename(__file__))

config, module_config, enabled_servers = load_config("moduleSatisfactory")


class Satisfactory(Extension):
    def __init__(self, client):
        self.channel: Optional[BaseChannel] = None
        self.message: Optional[Message] = None
        self.api: Optional[API] = None
        self.server_config: Optional[dict] = None

    @listen()
    async def on_startup(self):
        if not enabled_servers:
            logger.warning("moduleSatisfactory is not enabled for any server, skipping startup")
            return

        self.server_config = module_config[enabled_servers[0]]

        try:
            self.channel = await self.bot.fetch_channel(
                self.server_config["satisfactoryChannelId"]
            )
            self.message = await self.channel.fetch_message(
                self.server_config["satisfactoryMessageId"]
            )
            self.api = API(
                address=f"{self.server_config['satisfactoryServerIp']}:{self.server_config['satisfactoryServerPort']}",
                token=self.server_config["satisfactoryServerToken"],
            )
            self.update_message.start()
            await self.update_message()
        except Exception as e:
            logger.error("Failed to initialize Satisfactory extension: %s", e)

    @Task.create(IntervalTrigger(minutes=1))
    async def update_message(self):
        try:
            data = self.api.query_server_state()
            players = data["serverGameState"]["numConnectedPlayers"]
            tier = data["serverGameState"]["techTier"]

            embed = Embed(
                title="🏭 Serveur Satisfactory de la Coloc",
                description="Statut du serveur en temps réel",
                color=0x00A86B,
                timestamp=datetime.now(),
            )

            embed.add_field(
                name="📡 Informations de Connexion",
                value=(
                    f"**IP:** `{self.server_config['satisfactoryServerIp']}`\n"
                    f"**Port:** `{self.server_config['satisfactoryServerPort']}`\n"
                    f"**Mot de passe:** `{self.server_config['satisfactoryServerPassword']}`"
                ),
                inline=False,
            )

            embed.add_field(
                name="🎮 Statut du Jeu",
                value=f"**Tech Tier:** {tier}\n**Joueurs connectés:** {players}",
                inline=False,
            )

            await self.message.edit(content="", embed=embed)
            logger.debug("Updated Satisfactory status: %d players, tier %s", players, tier)
        except Exception as e:
            logger.error("Failed to update Satisfactory message: %s", e)
