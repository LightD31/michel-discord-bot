import asyncio
import re
from datetime import datetime

import requests
from interactions import (
    Client,
    Embed,
    Extension,
    IntervalTrigger,
    Task,
    listen,
)
from pyfactorybridge import API
from requests.adapters import HTTPAdapter

from src.core import clock
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


# pyfactorybridge's requests carry no timeout: a server that accepts the
# connection and then never answers held a worker thread for good, every later
# refresh was skipped (the task was "still running") and the process couldn't
# stop (the default executor is joined at exit). (connect, read) seconds:
REQUEST_TIMEOUT = (5.0, 10.0)
# Ceiling for one client call, which may make a few requests (the constructor
# checks the server's health, then authenticates).
CALL_TIMEOUT_SECONDS = 30.0


_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")


def normalize_fingerprint(value: object) -> str | None:
    """A SHA-256 fingerprint as 64 hex digits (``AB:CD:…`` accepted), else ``None``."""
    text = str(value or "").strip().lower()
    for prefix in ("sha256 fingerprint=", "sha256="):
        text = text.removeprefix(prefix)
    text = text.replace(":", "").replace(" ", "")
    return text if _FINGERPRINT_RE.match(text) else None


class _PinnedAdapter(HTTPAdapter):
    """Accepts the server only if its certificate has the configured SHA-256 fingerprint.

    pyfactorybridge sends every request with ``verify=False`` (a dedicated
    server's certificate is self-signed), so the API token went to whoever
    answered on the address. urllib3 checks ``assert_fingerprint`` whatever
    ``verify`` says, before any byte of the request is sent.
    """

    def __init__(self, fingerprint: str) -> None:
        self._fingerprint = fingerprint
        super().__init__()

    def init_poolmanager(self, *args, **kwargs):
        kwargs["assert_fingerprint"] = self._fingerprint
        return super().init_poolmanager(*args, **kwargs)


class _TimeoutSession(requests.Session):
    """A ``requests`` session whose requests give up after :data:`REQUEST_TIMEOUT`."""

    def request(self, method, url, *args, **kwargs):
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        return super().request(method, url, *args, **kwargs)


def _build_api(address: str, token: str, fingerprint: str) -> API:
    """``API(address=..., token=...)`` on a pinned session with timeouts (blocking).

    The constructor makes its requests before a session could be swapped in,
    so this does its three steps itself: the endpoint URL, a health check
    (which fails on an unreachable server, or one with another certificate)
    and the token.
    """
    api = API.__new__(API)
    api.URL = f"https://{address}/api/v1/"
    api.auth = None
    api.session = _TimeoutSession()
    api.session.mount("https://", _PinnedAdapter(fingerprint))
    api.get_server_health()
    api.renew_auth(method="token", value=token)
    return api


async def _call(fn, *args):
    """Run a blocking pyfactorybridge call off the loop, within :data:`CALL_TIMEOUT_SECONDS`."""
    return await asyncio.wait_for(asyncio.to_thread(fn, *args), CALL_TIMEOUT_SECONDS)


def _connection(settings: dict) -> tuple[str, str, str | None] | None:
    """``(address, token, fingerprint)`` of the guild's server, None until address and token are set."""
    ip = str(settings.get("satisfactoryServerIp") or "").strip()
    token = str(settings.get("satisfactoryServerToken") or "").strip()
    if not ip or not token:
        return None
    port = str(settings.get("satisfactoryServerPort") or "7777").strip()
    fingerprint = normalize_fingerprint(settings.get("satisfactoryServerCertificateSha256"))
    return f"{ip}:{port}", token, fingerprint


def _status_embed(settings: dict, players: int, tier: object) -> Embed:
    embed = Embed(
        title="🏭 Serveur Satisfactory de la Coloc",
        description="Statut du serveur en temps réel",
        color=0x00A86B,
        timestamp=clock.now(),
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
            api = await _call(_build_api, address, token, fingerprint)
            self._apis[key] = api
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
            data = await _call(api.query_server_state)
        except Exception:
            self._apis.pop(key, None)  # reconnect at the next refresh
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
