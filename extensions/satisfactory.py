import asyncio
from datetime import datetime

from interactions import (
    Client,
    Embed,
    Extension,
    IntervalTrigger,
    Task,
    listen,
)
from pyfactorybridge import API

from src.core import logging as logutil
from src.core.config import ModuleConfig
from src.core.logging import log_failure
from src.discord_ext.messages import GuildMessages, edit_message_if_changed
from src.webui.schemas import (
    SchemaBase,
    enabled_field,
    register_module,
    secret_field,
    ui,
)

# Where the server password and API token are sent: changing either means
# typing the secrets again (see ``secret_field(sent_to=...)``).
_SERVER_ADDRESS = ("satisfactoryServerIp", "satisfactoryServerPort")


@register_module("moduleSatisfactory")
class SatisfactoryConfig(SchemaBase):
    __label__ = "Satisfactory"
    __description__ = "Statut et gestion du serveur Satisfactory."
    __icon__ = "🏭"
    __category__ = "Esport & Jeux"

    enabled: bool = enabled_field()
    satisfactoryChannelId: str = ui(
        "Salon statut",
        "channel",
        required=True,
        description="Salon pour le message de statut (créé automatiquement).",
    )
    satisfactoryPinMessage: bool = ui(
        "Épingler le message de statut",
        "boolean",
        default=False,
        description="Épingler automatiquement le message de statut.",
    )
    satisfactoryServerIp: str = ui(
        "IP du serveur", "string", required=True, description="Adresse IP du serveur Satisfactory."
    )
    satisfactoryServerPort: str = ui(
        "Port du serveur", "string", default="7777", description="Port du serveur Satisfactory."
    )
    satisfactoryServerPassword: str | None = secret_field(
        "Mot de passe serveur",
        description="Mot de passe du serveur Satisfactory.",
        sent_to=_SERVER_ADDRESS,
    )
    satisfactoryServerToken: str | None = secret_field(
        "Token API serveur",
        description="Token d'authentification API du serveur.",
        sent_to=_SERVER_ADDRESS,
    )


logger = logutil.init_logger(__name__)

SATISFACTORY = ModuleConfig("moduleSatisfactory")


def _connection(settings: dict) -> tuple[str, str] | None:
    """``(address, token)`` of the guild's server, or None until both are configured."""
    ip = str(settings.get("satisfactoryServerIp") or "").strip()
    token = str(settings.get("satisfactoryServerToken") or "").strip()
    if not ip or not token:
        return None
    port = str(settings.get("satisfactoryServerPort") or "7777").strip()
    return f"{ip}:{port}", token


def _status_embed(settings: dict, players: int, tier: object) -> Embed:
    embed = Embed(
        title="🏭 Serveur Satisfactory de la Coloc",
        description="Statut du serveur en temps réel",
        color=0x00A86B,
        timestamp=datetime.now(),
    )
    embed.add_field(
        name="📡 Informations de Connexion",
        value=(
            f"**IP:** `{settings.get('satisfactoryServerIp', '')}`\n"
            f"**Port:** `{settings.get('satisfactoryServerPort', '')}`\n"
            f"**Mot de passe:** `{settings.get('satisfactoryServerPassword') or ''}`"
        ),
        inline=False,
    )
    embed.add_field(
        name="🎮 Statut du Jeu",
        value=f"**Tech Tier:** {tier}\n**Joueurs connectés:** {players}",
        inline=False,
    )
    return embed


class Satisfactory(Extension):
    """Each enabled guild's Satisfactory server status, refreshed every minute.

    The settings are read at each refresh, so a dashboard save (a new server,
    another channel) applies at the next one without a reload.
    """

    MODULE_KEYS = ("moduleSatisfactory",)

    def __init__(self, bot: Client):
        self.bot = bot
        self.messages = GuildMessages(
            bot,
            slot="satisfactory",
            label="Statut Satisfactory",
            channel_key="satisfactoryChannelId",
            pin_key="satisfactoryPinMessage",
            initial_content="Initialisation du statut Satisfactory…",
            logger=logger,
        )
        # One client per server: guilds watching the same one share it.
        self._apis: dict[tuple[str, str], API] = {}

    @listen()
    async def on_startup(self):
        if not self.update_message.running:
            self.update_message.start()
        await self.update_message()

    async def _api(self, address: str, token: str) -> API:
        api = self._apis.get((address, token))
        if api is None:
            # pyfactorybridge is synchronous (requests), and its constructor
            # already checks the server and authenticates: off the loop.
            api = await asyncio.to_thread(API, address=address, token=token)
            self._apis[(address, token)] = api
        return api

    @Task.create(IntervalTrigger(minutes=1))
    async def update_message(self):
        guilds = SATISFACTORY.items()
        self.messages.keep_only([guild_id for guild_id, _ in guilds])
        for guild_id, settings in guilds:
            try:
                await self._refresh(guild_id, settings)
            except Exception as e:  # noqa: BLE001 — one server down must not stop the others
                log_failure(
                    logger, "Failed to update Satisfactory status for guild %s: %s", guild_id, e
                )

    async def _refresh(self, guild_id: str, settings: dict) -> None:
        connection = _connection(settings)
        if connection is None:
            logger.debug("Guild %s: Satisfactory server not configured; skipping", guild_id)
            return
        message = await self.messages.get(guild_id, settings)
        if message is None:
            return
        try:
            api = await self._api(*connection)
            data = await asyncio.to_thread(api.query_server_state)
        except Exception:
            self._apis.pop(connection, None)  # reconnect at the next refresh
            raise
        players = data["serverGameState"]["numConnectedPlayers"]
        tier = data["serverGameState"]["techTier"]
        await edit_message_if_changed(
            message,
            content="",
            embed=_status_embed(settings, players, tier),
            ignore_timestamp=True,
            logger=logger,
        )
        logger.debug("Updated Satisfactory status: %d players, tier %s", players, tier)
