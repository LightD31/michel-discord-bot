"""ApiMixin — VLR.gg API fetch methods and data parsing logic."""

from typing import Any

from interactions import Embed

from features.vlrgg import VlrggLiveRepository, match_id_of
from features.vlrgg import (
    fetch_all_team_data as vlrgg_fetch_all,
)

from ._common import TeamConfig, logger


class ApiMixin:
    """Mixin providing VLR.gg data-fetching and parsing helpers."""

    # ── Fetch helpers ─────────────────────────────────────────────────────────

    async def _fetch_team_schedule(
        self, tc: TeamConfig
    ) -> tuple[list[Embed], dict[str, Any], dict[str, Any]]:
        """Récupère le planning, les matchs en cours et les données brutes via VLR.gg."""
        vlr_data = await self._fetch_vlrgg_data(tc)
        if not vlr_data:
            logger.warning(f"{tc.name}: aucune donnée VLR.gg disponible")
            return [], {}, {}

        embeds = self._build_vlrgg_embeds(vlr_data, tc.name)
        ongoing = self._extract_vlrgg_ongoing(vlr_data)
        return embeds, ongoing, vlr_data

    async def _fetch_vlrgg_data(self, tc: TeamConfig) -> dict[str, Any] | None:
        """Récupère les données VLR.gg pour une équipe."""
        if not tc.vlr_team_id:
            logger.warning(f"{tc.name}: aucun vlrTeamId configuré")
            return None
        try:
            data = await vlrgg_fetch_all(tc.vlr_team_id, tc.name)
            total = (
                len(data.get("results", []))
                + len(data.get("upcoming", []))
                + len(data.get("live", []))
            )
            if total == 0:
                logger.debug(f"VLR.gg: aucune donnée trouvée pour {tc.name}")
                return None
            return data
        except Exception as e:
            logger.error(f"Erreur récupération VLR.gg pour {tc.name}: {e}")
            return None

    # ── Data parsing ──────────────────────────────────────────────────────────

    def _extract_vlrgg_ongoing(self, vlr_data: dict[str, Any]) -> dict[str, Any]:
        """Extrait les matchs en cours depuis les données VLR.gg."""
        ongoing: dict[str, Any] = {}
        for match in vlr_data.get("live", []):
            match_id = self._make_vlr_match_id(match)
            ongoing[match_id] = match
        return ongoing

    @staticmethod
    def _make_vlr_match_id(match: dict[str, Any]) -> str:
        """Génère un ID unique pour un match VLR.gg (format persisté en base)."""
        url_id = match_id_of(match)
        if url_id:
            return f"vlrgg_{url_id}"
        return f"vlrgg_{match.get('team1', '')}_{match.get('team2', '')}"

    # ── Mongo persistence ─────────────────────────────────────────────────────

    def _live_repo(self, server_id: str) -> VlrggLiveRepository:
        return VlrggLiveRepository(server_id)

    async def _save_live_match(
        self,
        server_id: str,
        team_name: str,
        match_id: str,
        match_data: dict[str, Any],
        channel_id: str,
        message_id: str,
    ) -> None:
        try:
            await self._live_repo(server_id).upsert(
                {
                    "_id": match_id,
                    "team_name": team_name,
                    "channel_id": channel_id,
                    "message_id": message_id,
                    "match_data": match_data,
                }
            )
        except Exception as e:
            logger.warning(f"Impossible de persister le match live {match_id}: {e}")

    async def _delete_live_match(self, server_id: str, match_id: str) -> None:
        try:
            await self._live_repo(server_id).delete(match_id)
        except Exception as e:
            logger.warning(f"Impossible de supprimer le match live persisté {match_id}: {e}")

    async def _restore_live_state(self, server_id: str, server_state: Any) -> None:
        """Restaure les matchs live depuis MongoDB après un redémarrage."""
        try:
            docs = await self._live_repo(server_id).load_all()
        except Exception as e:
            logger.warning(f"Serveur {server_id}: impossible de charger l'état live: {e}")
            return

        for doc in docs:
            match_id = doc["_id"]
            team_name = doc.get("team_name", "")
            channel_id = doc.get("channel_id", "")
            message_id = doc.get("message_id", "")
            match_data = doc.get("match_data", {})

            team_state = server_state.teams.get(team_name)
            if not team_state:
                logger.warning(
                    f"Serveur {server_id}: équipe '{team_name}' introuvable pour restauration"
                )
                continue

            try:
                channel = await self.bot.fetch_channel(channel_id)
                if not channel or not hasattr(channel, "fetch_message"):
                    raise ValueError(f"Canal {channel_id} invalide ou sans fetch_message")
                message = await channel.fetch_message(message_id)
                team_state.live_messages[match_id] = message
                team_state.ongoing_matches[match_id] = match_data
                logger.info(f"Serveur {server_id}: match live {match_id} restauré pour {team_name}")
            except Exception as e:
                logger.warning(
                    f"Serveur {server_id}: impossible de restaurer le message {message_id}: {e}"
                )
                await self._delete_live_match(server_id, match_id)
