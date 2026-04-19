"""TasksMixin — background tasks (polling, state persistence, HTTP client) for the Olympics extension."""

import asyncio
from functools import partial
from typing import Any

from curl_cffi import requests as cffi_requests
from interactions import IntervalTrigger, Task

from ._common import (
    COUNTRY_CODE,
    EVENT_MEDALS_URL,
    MEDALLISTS_URL,
    MEDALS_URL,
    POLL_INTERVAL_MINUTES,
    _olympics_col,
    logger,
)


class TasksMixin:
    """Mixin providing background polling tasks and HTTP/persistence helpers."""

    # ─── HTTP Client dédié Olympics ──────────────────────────────────────────────

    async def _olympics_fetch(self, url: str, retries: int = 3) -> dict:
        """Effectue une requête GET vers l'API Olympics.com.

        Utilise curl_cffi pour impersonner le fingerprint TLS de Chrome,
        nécessaire pour contourner le WAF d'olympics.com.

        Args:
            url: URL de l'API.
            retries: Nombre de tentatives.

        Returns:
            Données JSON.
        """
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://www.olympics.com/fr/olympic-games/milan-cortina-2026/medals",
            "Origin": "https://www.olympics.com",
        }

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

        raise Exception(f"Impossible de récupérer {url} après {retries} tentatives")

    # ─── Persistance ──────────────────────────────────────────────────────────

    async def _load_state(self) -> None:
        """Charge l'état des médailles déjà notifiées depuis MongoDB."""
        try:
            doc = await _olympics_col.find_one({"_id": "known_medals"})
            if doc:
                self.known_medals = set(doc.get("medals", []))
                logger.info(f"État Olympics chargé : {len(self.known_medals)} médailles connues")
            else:
                logger.info("Aucun état Olympics trouvé, première exécution")
        except Exception as e:
            logger.error(f"Erreur lors du chargement de l'état Olympics : {e}")

    async def _save_state(self) -> None:
        """Sauvegarde l'état des médailles notifiées dans MongoDB."""
        try:
            await _olympics_col.update_one(
                {"_id": "known_medals"},
                {"$set": {"medals": list(self.known_medals)}},
                upsert=True,
            )
        except Exception as e:
            logger.error(f"Erreur lors de la sauvegarde de l'état Olympics : {e}")

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
            logger.error(f"Erreur lors de l'initialisation des médailles : {e}")

    # ─── Récupération de données supplémentaires ──────────────────────────────

    async def _fetch_all_medallists(self) -> list[dict[str, Any]]:
        """Récupère la liste de tous les médaillés.

        Returns:
            Liste de tous les athlètes médaillés.
        """
        data = await self._olympics_fetch(MEDALLISTS_URL)
        return data.get("athletes", [])

    async def _fetch_event_medals(self) -> dict[str, Any]:
        """Récupère les médailles par épreuve.

        Returns:
            Données des médailles par discipline/épreuve.
        """
        data = await self._olympics_fetch(EVENT_MEDALS_URL)
        return data.get("eventMedals", {})

    # ─── Tâche planifiée ──────────────────────────────────────────────────────

    @Task.create(IntervalTrigger(minutes=POLL_INTERVAL_MINUTES))
    async def check_medals(self) -> None:
        """Vérifie périodiquement les nouvelles médailles françaises."""
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

                for medal in new_medals:
                    embed = self._build_medal_alert_embed(medal, france_standing)
                    if self.channel:
                        await self.channel.send(embeds=[embed])
                        await asyncio.sleep(1)  # Petite pause entre les messages
            else:
                logger.debug("Aucune nouvelle médaille pour la France")

        except Exception as e:
            logger.exception(f"Erreur lors de la vérification des médailles : {e}")
