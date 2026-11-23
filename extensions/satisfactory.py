from datetime import datetime

from interactions import (
    Client,
    ContainerComponent,
    Extension,
    IntervalTrigger,
    MessageFlags,
    Task,
    listen,
)
from pyfactorybridge import API

from src.core import clock, log_context
from src.core import logging as logutil
from src.core.config import ModuleConfig
from src.core.logging import log_failure
from src.discord_ext.look import card_v2
from src.discord_ext.messages import GuildMessages, edit_message_if_changed
from src.integrations.satisfactory import build_api, call, connection_of, normalize_fingerprint
from src.webui.schemas import (
    ModuleHelp,
    SchemaBase,
    enabled_field,
    register_module,
    secret_field,
    ui,
)

# Where the server password and API token are sent, and the certificate that
# proves it's that server: changing any means typing the secrets again (see
# ``secret_field(sent_to=...)``).
_SERVER_ADDRESS = (
    "satisfactoryServerIp",
    "satisfactoryServerPort",
    "satisfactoryServerCertificateSha256",
)


@register_module("moduleSatisfactory")
class SatisfactoryConfig(SchemaBase):
    __label__ = "Satisfactory"
    __description__ = "Statut et gestion du serveur Satisfactory."
    __icon__ = "🏭"
    __category__ = "Esport & Jeux"
    __help__ = ModuleHelp(
        "Le statut du serveur Satisfactory.",
        (),
        "Le message de statut se met à jour tout seul.",
    )

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
    satisfactoryServerCertificateSha256: str | None = ui(
        "Empreinte du certificat (SHA-256)",
        "string",
        description=(
            "Empreinte SHA-256 du certificat du serveur, que le bot vérifie avant d'envoyer "
            "le token API (le certificat d'un serveur dédié est auto-signé). Sur l'hôte : "
            "openssl s_client -connect IP:PORT </dev/null 2>/dev/null | "
            "openssl x509 -noout -fingerprint -sha256. Sans elle, le statut n'est pas lu."
        ),
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


def _status_card(settings: dict, players: int, tier: object) -> ContainerComponent:
    """The server's status as a Components V2 card (P22): no timestamp, nothing to diff away."""
    return card_v2(
        "moduleSatisfactory",
        title="🏭 Serveur Satisfactory de la Coloc",
        blocks=[
            "**📡 Connexion**\n"
            f"IP : `{settings.get('satisfactoryServerIp', '')}`\n"
            f"Port : `{settings.get('satisfactoryServerPort', '')}`\n"
            f"Mot de passe : `{settings.get('satisfactoryServerPassword') or ''}`",
            f"**🎮 Partie**\nPalier technologique : {tier}\nJoueurs connectés : {players}",
        ],
        footer="statut actualisé chaque minute",
    )


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
            v2=True,
        )
        # One client per server: guilds watching the same one share it.
        self._apis: dict[tuple[str, str, str], API] = {}
        self._unpinned_warned: set[str] = set()

    @listen()
    async def on_startup(self):
        if not self.update_message.running:
            self.update_message.start()
        await self.update_message()

    async def _api(self, address: str, token: str, fingerprint: str) -> API:
        key = (address, token, fingerprint)
        api = self._apis.get(key)
        if api is None:
            # pyfactorybridge is synchronous (requests), and building a client
            # already checks the server and authenticates: off the loop.
            api = await call(build_api, address, token, fingerprint)
            self._apis[key] = api
        return api

    @Task.create(IntervalTrigger(minutes=1))
    async def update_message(self):
        guilds = SATISFACTORY.items()
        self.messages.keep_only([guild_id for guild_id, _ in guilds])
        for guild_id, settings in guilds:
            with log_context.scope(guild_id=guild_id):
                try:
                    await self._refresh(guild_id, settings)
                except Exception as e:  # noqa: BLE001 — one server down must not stop the others
                    log_failure(
                        logger, "Failed to update Satisfactory status for guild %s: %s", guild_id, e
                    )

    async def _refresh(self, guild_id: str, settings: dict) -> None:
        connection = connection_of(settings)
        if connection is None:
            logger.debug("Guild %s: Satisfactory server not configured; skipping", guild_id)
            return
        address, token, fingerprint = connection
        if fingerprint is None:
            # The token isn't sent to a server whose certificate isn't pinned.
            if guild_id not in self._unpinned_warned:
                self._unpinned_warned.add(guild_id)
                logger.warning(
                    "Guild %s: no valid satisfactoryServerCertificateSha256, Satisfactory "
                    "status not read (the API token is only sent to a pinned certificate)",
                    guild_id,
                )
            return
        self._unpinned_warned.discard(guild_id)
        message = await self.messages.get(guild_id, settings)
        if message is None:
            return
        key = (address, token, fingerprint)
        try:
            api = await self._api(*key)
            data = await call(api.query_server_state)
        except Exception:
            self._apis.pop(key, None)  # reconnect at the next refresh
            raise
        players = data["serverGameState"]["numConnectedPlayers"]
        tier = data["serverGameState"]["techTier"]
        await edit_message_if_changed(
            message,
            components=[_status_card(settings, players, tier)],
            flags=MessageFlags.IS_COMPONENTS_V2,
            logger=logger,
        )
        logger.debug("Updated Satisfactory status: %d players, tier %s", players, tier)
