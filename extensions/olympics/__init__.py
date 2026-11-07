"""Extension Discord pour le suivi des médailles des JO d'hiver Milan-Cortina 2026.

Cette extension surveille les nouvelles médailles françaises et envoie
des alertes automatiques dans un canal Discord configuré.

The class is assembled as a mixin composition so that each concern lives in its
own module (``medals``, ``tasks``). Shared data, constants, and helpers are in
:mod:`._common`.
"""

from interactions import Client, Extension, listen

from ._common import OLYMPICS, logger
from .medals import MedalsMixin
from .tasks import TasksMixin


class OlympicsExtension(MedalsMixin, TasksMixin, Extension):
    """Discord extension for tracking JO d'hiver Milan-Cortina 2026 medals.

    Composes MedalsMixin (slash commands + embed builders) and TasksMixin
    (background polling, HTTP client, state persistence).
    """

    MODULE_KEYS = ("moduleOlympics",)

    def __init__(self, bot: Client) -> None:
        self.bot: Client = bot
        # État : ensemble des clés de médailles déjà connues
        # Format : "{eventCode}_{medalType}_{competitorCode}"
        self.known_medals: set[str] = set()

    @listen()
    async def on_startup(self) -> None:
        """Charge l'état des médailles et démarre la tâche de surveillance.

        Le salon de chaque serveur est lu à chaque passage de la tâche.
        Activer le module dans un serveur recharge l'extension, qui repasse ici.
        """
        if not OLYMPICS.guild_ids():
            logger.info("moduleOlympics n'est activé sur aucun serveur : pas de surveillance")
            return
        try:
            await self._load_state()

            # Initialisation silencieuse : enregistrer les médailles existantes
            # sans envoyer de notifications
            if not self.known_medals:
                await self._initialize_known_medals()

            if not self.check_medals.running:
                self.check_medals.start()
            logger.info("Tâche de surveillance des médailles Olympics démarrée")
        except Exception as e:
            logger.exception(f"Erreur lors de l'initialisation Olympics : {e}")


def setup(bot: Client) -> None:
    OlympicsExtension(bot)
