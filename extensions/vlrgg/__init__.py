"""Extension Esport Tracker pour le suivi des matchs Valorant via VLR.gg.

Cette extension permet de suivre les matchs de plusieurs équipes Valorant
via l'API VLR.gg (source unique).

Configuration par serveur via le dashboard web (moduleVlrgg):
- notificationChannelId: salon pour les notifications live
- teams: liste d'équipes, chacune avec:
    - name: nom de l'équipe
    - vlrTeamId: ID VLR.gg (requis)
    - channelMessageId: "channelId:messageId" pour le planning (optionnel)

The class is assembled as a mixin composition so that each concern lives in its
own module (``api``, ``embeds``, ``notifications``). Shared data classes,
constants, and helpers are in :mod:`._common`.
"""

from interactions import Client, Extension, listen

from ._common import (
    ServerState,
    TeamConfig,
    TeamState,
    _save_team_channel_message,
    enabled_servers,
    logger,
    module_config,
)
from .api import ApiMixin
from .embeds import EmbedsMixin
from .notifications import NotificationsMixin


class VlrggExtension(ApiMixin, EmbedsMixin, NotificationsMixin, Extension):
    """Discord extension combining VLR.gg API, embeds, and notification behaviours."""

    def __init__(self, bot: Client):
        self.bot: Client = bot
        self._servers: dict[str, ServerState] = {}

    @listen()
    async def on_startup(self) -> None:
        """Initialise les états par serveur et démarre les tâches planifiées."""
        try:
            await self._initialize_all_servers()
            self.schedule.start()
            self.live_update.start()
        except Exception as e:
            logger.error(f"Erreur lors de l'initialisation: {e}")

    async def _initialize_all_servers(self) -> None:
        """Initialise les messages et canaux pour tous les serveurs activés."""
        for server_id in enabled_servers:
            srv_config = module_config.get(server_id, {})
            teams_raw = srv_config.get("teams", [])

            if not teams_raw:
                logger.warning(f"Serveur {server_id}: aucune équipe configurée")
                continue

            server_state = ServerState(
                server_id=server_id,
                notification_channel_id=srv_config.get("notificationChannelId"),
            )

            # Charger le canal de notification du serveur
            if server_state.notification_channel_id:
                try:
                    server_state.notification_channel = await self.bot.fetch_channel(
                        server_state.notification_channel_id
                    )
                except Exception as e:
                    logger.warning(
                        f"Serveur {server_id}: impossible de charger le canal de notification: {e}"
                    )

            # Initialiser chaque équipe
            for team_raw in teams_raw:
                team_cfg = TeamConfig.from_dict(team_raw)
                team_state = TeamState(
                    team_config=team_cfg,
                    server_id=server_id,
                    notification_channel=server_state.notification_channel,
                )

                # Charger le message de planning; créer si manquant et channel connu
                if team_cfg.channel_id:
                    try:
                        channel = await self.bot.fetch_channel(team_cfg.channel_id)
                        if channel and hasattr(channel, "send"):
                            msg = None
                            if team_cfg.message_id:
                                try:
                                    msg = await channel.fetch_message(team_cfg.message_id)
                                except Exception as e:
                                    logger.warning(
                                        f"Serveur {server_id}: message {team_cfg.message_id} "
                                        f"introuvable pour {team_cfg.name} ({e}); recréation"
                                    )
                            if msg is None:
                                msg = await channel.send(
                                    f"Initialisation du planning de {team_cfg.name}…"
                                )
                                if team_cfg.pin:
                                    try:
                                        await msg.pin()
                                    except Exception as e:
                                        logger.warning("Impossible d'épingler: %s", e)
                                _save_team_channel_message(
                                    server_id,
                                    team_cfg.name,
                                    str(channel.id),
                                    str(msg.id),
                                )
                                team_cfg.message_id = str(msg.id)
                            team_state.schedule_message = msg
                            logger.info(
                                f"Serveur {server_id}: message de planning prêt pour {team_cfg.name}"
                            )
                    except Exception as e:
                        logger.warning(
                            f"Serveur {server_id}: impossible d'initialiser le message de planning "
                            f"pour {team_cfg.name}: {e}"
                        )

                server_state.teams[team_cfg.name] = team_state

            self._servers[server_id] = server_state

            # Restaurer les matchs live persistés
            await self._restore_live_state(server_id, server_state)

            logger.info(
                f"Serveur {server_id}: {len(server_state.teams)} équipe(s) initialisée(s) "
                f"({', '.join(server_state.teams.keys())})"
            )


def setup(bot: Client):
    VlrggExtension(bot)
