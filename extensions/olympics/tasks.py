"""TasksMixin — background tasks (polling, state persistence, HTTP client) for the Olympics extension."""

import asyncio
from functools import partial
from typing import Any

from curl_cffi import requests as cffi_requests
from interactions import IntervalTrigger, Task

from features.olympics import OlympicsStateRepository
from src.core.errors import IntegrationError
from src.core.logging import log_failure

from ._common import (
    COUNTRY_CODE,
    EVENT_MEDALS_PATH,
    MEDALLISTS_PATH,
    MEDALS_PATH,
    OLYMPICS,
    POLL_INTERVAL_MINUTES,
    logger,
    olympics_api_url,
    olympics_headers,
)


class TasksMixin:
    """Mixin providing background polling tasks and HTTP/persistence helpers."""

    # ─── HTTP Client dédié Olympics ──────────────────────────────────────────────

    async def _olympics_fetch(self, path: str, retries: int = 3) -> dict:
        """Effectue une requête GET vers l'API Olympics.com configurée.

        Utilise curl_cffi pour impersonner le fingerprint TLS de Chrome,
        nécessaire pour contourner le WAF d'olympics.com.

        Args:
            path: Endpoint, relatif à l'URL de l'API (section globale « olympics »).
            retries: Nombre de tentatives.

        Returns:
            Données JSON.
        """
        base = olympics_api_url()
        if not base:
            raise IntegrationError("Aucune URL d'API Olympics configurée (olympics.apiUrl)")
        url = f"{base}/{path}"
        headers = olympics_headers()

        for attempt in range(retries):
            try:
                response = await asyncio.to_thread(
                    partial(
                        cffi_requests.get,
                        url,
                        headers=headers,
                        impersonate="chrome",
                        timeout=30,
                    )
                )
                if response.status_code == 200:
                    return response.json()
                logger.warning(
                    f"Olympics API {url} - status {response.status_code} (tentative {attempt + 1}/{retries})"
                )
            except Exception as e:
                logger.warning(f"Olympics API erreur: {e} (tentative {attempt + 1}/{retries})")

            if attempt < retries - 1:
                await asyncio.sleep(2**attempt)  # Backoff exponentiel

        raise IntegrationError(f"Impossible de récupérer {url} après {retries} tentatives")

    # ─── Persistance ──────────────────────────────────────────────────────────

    _state_repo = OlympicsStateRepository()

    async def _load_state(self) -> None:
        """Charge l'état des médailles déjà notifiées depuis MongoDB."""
        try:
            medals = await self._state_repo.load_known_medals()
            if medals is not None:
                self.known_medals = medals
                logger.info(f"État Olympics chargé : {len(self.known_medals)} médailles connues")
            else:
                logger.info("Aucun état Olympics trouvé, première exécution")
        except Exception as e:
            log_failure(logger, f"Erreur lors du chargement de l'état Olympics : {e}")

    async def _save_state(self) -> None:
        """Sauvegarde l'état des médailles notifiées dans MongoDB."""
        try:
            await self._state_repo.save_known_medals(self.known_medals)
        except Exception as e:
            log_failure(logger, f"Erreur lors de la sauvegarde de l'état Olympics : {e}")

    # ─── Initialisation silencieuse ───────────────────────────────────────────

    async def _initialize_known_medals(self) -> None:
        """Enregistre silencieusement les médailles déjà existantes au démarrage."""
        try:
            medals = await self._fetch_france_medals()
            for medal in medals:
                key = self._medal_key(medal)
                self.known_medals.add(key)
            await self._save_state()
            logger.info(
                f"Initialisation : {len(self.known_medals)} médailles FRA existantes enregistrées"
            )
        except Exception as e:
            log_failure(logger, f"Erreur lors de l'initialisation des médailles : {e}")

    # ─── Récupération de données supplémentaires ──────────────────────────────

    async def _fetch_all_medallists(self) -> list[dict[str, Any]]:
        """Récupère la liste de tous les médaillés.

        Returns:
            Liste de tous les athlètes médaillés.
        """
        data = await self._olympics_fetch(MEDALLISTS_PATH)
        return data.get("athletes", [])

    async def _fetch_event_medals(self) -> dict[str, Any]:
        """Récupère les médailles par épreuve.

        Returns:
            Données des médailles par discipline/épreuve.
        """
        data = await self._olympics_fetch(EVENT_MEDALS_PATH)
        return data.get("eventMedals", {})

    async def _send_alerts(
        self,
        guild_id: str,
        settings: dict[str, Any],
        medals: list[dict[str, Any]],
        france_standing: dict[str, Any] | None,
    ) -> None:
        """Poste les alertes dans le salon configuré du serveur."""
        channel_id = settings.get("olympicsChannelId")
        if not channel_id:
            logger.warning("olympicsChannelId non configuré pour le serveur %s", guild_id)
            return
        try:
            channel = await self.bot.fetch_channel(channel_id)
            logo_url = settings.get("olympicsLogoUrl") or ""
            for medal in medals:
                embed = self._build_medal_alert_embed(medal, france_standing, logo_url)
                await channel.send(embeds=[embed])
                await asyncio.sleep(1)  # Petite pause entre les messages
        except Exception as e:  # noqa: BLE001 — un serveur en échec n'arrête pas les autres
            log_failure(logger, "Alertes Olympics impossibles pour le serveur %s : %s", guild_id, e)

    # ─── Tâche planifiée ──────────────────────────────────────────────────────

    @Task.create(IntervalTrigger(minutes=POLL_INTERVAL_MINUTES))
    async def check_medals(self) -> None:
        """Vérifie périodiquement les nouvelles médailles françaises.

        Les alertes partent dans le salon de chaque serveur où le module est
        activé, lu à chaque passage : un changement dans le dashboard
        s'applique sans recharger l'extension.
        """
        if not OLYMPICS.guild_ids():
            return
        if not olympics_api_url():
            logger.warning("Aucune URL d'API Olympics (olympics.apiUrl) : vérification sautée")
            return
        logger.debug("Vérification des médailles Olympics...")
        try:
            medals = await self._fetch_france_medals()
            new_medals = []

            for medal in medals:
                key = self._medal_key(medal)
                if key not in self.known_medals:
                    new_medals.append(medal)
                    self.known_medals.add(key)

            if new_medals:
                logger.info(
                    f"{len(new_medals)} nouvelle(s) médaille(s) détectée(s) pour la France !"
                )
                await self._save_state()

                # Récupérer le classement à jour pour le contexte
                standings = await self._fetch_medal_standings()
                france_standing = self._get_country_standing(standings, COUNTRY_CODE)

                for guild_id, settings in OLYMPICS.items():
                    await self._send_alerts(guild_id, settings, new_medals, france_standing)
            else:
                logger.debug("Aucune nouvelle médaille pour la France")

        except Exception as e:
            logger.exception(f"Erreur lors de la vérification des médailles : {e}")
