from interactions import (
    Message,
    Extension,
    Task,
    BaseChannel,
    listen,
    IntervalTrigger,
    Client,
    Embed,
)
from typing import Optional
from src.utils import load_config
from pyfactorybridge import API
from datetime import datetime

config, module_config, enabled_servers = load_config("moduleSatisfactory")
module_config = module_config[enabled_servers[0]]


class Satisfactory(Extension):
    def init(self, client: Client):
        self.bot: Client = client
        self.channel: Optional[BaseChannel] = None
        self.message: Optional[Message] = None
        self.api: Optional[API] = None

    @listen()
    async def on_startup(self):
        self.channel = await self.bot.fetch_channel(
            module_config["satisfactoryChannelId"]
        )
        self.message = await self.channel.fetch_message(
            module_config["satisfactoryMessageId"]
        )
        self.api = API(
            address=f"{module_config['satisfactoryServerIp']}:{module_config['satisfactoryServerPort']}",
            token=module_config["satisfactoryServerToken"],
        )
        self.update_message.start()
        await self.update_message()

    @Task.create(IntervalTrigger(minutes=1))
    async def update_message(self):
        data = self.api.query_server_state()
        players = data["serverGameState"]["numConnectedPlayers"]  # Nombre de joueurs
        tier = data["serverGameState"]["techTier"]

        embed = Embed(
            title="🏭 Serveur Satisfactory de la Coloc",
            description="Statut du serveur en temps réel",
            color=0x00A86B,  # A nice green color
            timestamp=datetime.now()
        )

        # Server Info
        embed.add_field(
            name="📡 Informations de Connexion",
            value=(
                f"**IP:** `{module_config['satisfactoryServerIp']}`\n"
                f"**Port:** `{module_config['satisfactoryServerPort']}`\n"
                f"**Mot de passe:** `{module_config['satisfactoryServerPassword']}`"
            ),
            inline=False,
        )

        # Game Info
        embed.add_field(
            name="🎮 Statut du Jeu",
            value=(f"**Tech Tier:** {tier}\n" f"**Joueurs connectés:** {players}"),
            inline=False,
        )
        await self.message.edit(content="", embed=embed)
