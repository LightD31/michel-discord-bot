"""Extension Discord pour le suivi des médailles des JO d'hiver Milan-Cortina 2026.

Cette extension surveille les nouvelles médailles françaises et envoie
des alertes automatiques dans un canal Discord configuré.

The class is assembled as a mixin composition so that each concern lives in its
own module (``medals``, ``tasks``). Shared data, constants, and helpers are in
:mod:`._common`.
"""

from interactions import Client, Extension, listen

from ._common import enabled_servers, logger, module_config
from .medals import MedalsMixin
from .tasks import TasksMixin


class OlympicsExtension(MedalsMixin, TasksMixin, Extension):
    """Discord extension for tracking JO d'hiver Milan-Cortina 2026 medals.

    Composes MedalsMixin (slash commands + embed builders) and TasksMixin
    (background polling, HTTP client, state persistence).
    """

    def __init__(self, bot: Client) -> None:
        self.bot: Client = bot
        self.channel = None
        # État : ensemble des clés de médailles déjà connues
        # Format : "{eventCode}_{medalType}_{competitorCode}"
        self.known_medals: set[str] = set()

    @listen()
    async def on_startup(self) -> None:
        """Initialise le canal et démarre la tâche de surveillance."""
        if not enabled_servers:
            logger.warning("moduleOlympics is not enabled for any server, skipping startup")
            return
        try:
            await self._load_state()
            channel_id = module_config.get("olympicsChannelId")
            if channel_id:
                self.channel = await self.bot.fetch_channel(channel_id)
                logger.info(f"Canal Olympics initialisé : {self.channel.name}")
            else:
                logger.error("olympicsChannelId non configuré dans moduleOlympics")
                return

            # Initialisation silencieuse : enregistrer les médailles existantes
            # sans envoyer de notifications
            if not self.known_medals:
                await self._initialize_known_medals()

            self.check_medals.start()
            logger.info("Tâche de surveillance des médailles Olympics démarrée")
        except Exception as e:
            logger.exception(f"Erreur lors de l'initialisation Olympics : {e}")


def setup(bot: Client) -> None:
    OlympicsExtension(bot)
