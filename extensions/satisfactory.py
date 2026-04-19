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
from src.helpers import fetch_or_create_persistent_message
from src.utils import load_config

logger = logutil.init_logger(os.path.basename(__file__))

config, module_config, enabled_servers = load_config("moduleSatisfactory")


class Satisfactory(Extension):
    def __init__(self, client):
        self.channel: BaseChannel | None = None
        self.message: Message | None = None
        self.api: API | None = None
        self.server_config: dict | None = None
        self.guild_id: str | None = None

    async def _ensure_message(self) -> Message | None:
        if self.message is not None:
            return self.message
        if not self.server_config:
            return None
        self.message = await fetch_or_create_persistent_message(
            self.bot,
            channel_id=self.server_config.get("satisfactoryChannelId"),
            message_id=self.server_config.get("satisfactoryMessageId"),
            module_name="moduleSatisfactory",
            message_id_key="satisfactoryMessageId",
            guild_id=self.guild_id,
            initial_content="Initialisation du statut Satisfactory…",
            pin=bool(self.server_config.get("satisfactoryPinMessage", False)),
            logger=logger,
        )
        return self.message

    @listen()
    async def on_startup(self):
        if not enabled_servers:
            logger.warning("moduleSatisfactory is not enabled for any server, skipping startup")
            return

        self.guild_id = enabled_servers[0]
        self.server_config = module_config[self.guild_id]

        try:
            await self._ensure_message()
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
            message = await self._ensure_message()
            if message is None or self.api is None:
                logger.debug("Satisfactory message or API not ready; skipping")
                return
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

            await message.edit(content="", embed=embed)
            logger.debug("Updated Satisfactory status: %d players, tier %s", players, tier)
        except Exception as e:
            logger.error("Failed to update Satisfactory message: %s", e)
