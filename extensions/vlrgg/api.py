"""ApiMixin — VLR.gg API fetch methods and data parsing logic."""

from typing import Any

from interactions import Embed

from src.vlrgg import (
    extract_match_id_from_url,
    fetch_match_details,
)
from src.vlrgg import (
    fetch_all_team_data as vlrgg_fetch_all,
)

from ._common import TeamConfig, logger


class ApiMixin:
    """Mixin providing VLR.gg data-fetching and parsing helpers."""

    # ── Fetch helpers ─────────────────────────────────────────────────────────

    async def _fetch_team_schedule(self, tc: TeamConfig) -> tuple[list[Embed], dict[str, Any]]:
        """Récupère le planning avec suivi des matchs en cours via VLR.gg."""
        vlr_data = await self._fetch_vlrgg_data(tc)
        if not vlr_data:
            logger.warning(f"{tc.name}: aucune donnée VLR.gg disponible")
            return [], {}

        embeds = self._build_vlrgg_embeds(vlr_data, tc.name)
        ongoing = self._extract_vlrgg_ongoing(vlr_data)
        return embeds, ongoing

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
        """Génère un ID unique pour un match VLR.gg."""
        match_url = match.get("match_page", "")
        url_id = extract_match_id_from_url(match_url)
        if url_id:
            return f"vlrgg_{url_id}"
        return f"vlrgg_{match.get('team1', '')}_{match.get('team2', '')}"

    # ── Match details formatting ──────────────────────────────────────────────

    def _format_match_details_maps(self, details: dict[str, Any]) -> str | None:
        """Formate les scores par map depuis les détails d'un match."""
        maps_data = details.get("maps", [])
        if not maps_data:
            return None

        lines = []
        for map_info in maps_data:
            map_name = map_info.get("map_name", map_info.get("map", "???"))
            if map_name.lower() in ("tbd", "n/a", ""):
                continue

            score_data = map_info.get("score", {})
            if isinstance(score_data, dict):
                t1_score = score_data.get("team1", "?")
                t2_score = score_data.get("team2", "?")
            else:
                t1_score = map_info.get("team1_score", "?")
                t2_score = map_info.get("team2_score", "?")

            try:
                s1, s2 = int(t1_score), int(t2_score)
                if s1 > s2:
                    score_str = f"**{s1}**-{s2}"
                elif s2 > s1:
                    score_str = f"{s1}-**{s2}**"
                else:
                    score_str = f"{s1}-{s2}"
            except (ValueError, TypeError):
                score_str = f"{t1_score}-{t2_score}"

            duration = map_info.get("duration", "")
            dur_str = f" ({duration})" if duration else ""
            lines.append(f"**{map_name}**: {score_str}{dur_str}")

        return "\n".join(lines) if lines else None

    def _format_match_details_top_players(self, details: dict[str, Any]) -> str | None:
        """Formate les meilleurs joueurs depuis les détails d'un match."""
        all_players: dict[str, dict[str, Any]] = {}
        maps_data = details.get("maps", [])

        for map_info in maps_data:
            players_data = map_info.get("players", {})
            if isinstance(players_data, dict):
                player_lists = list(players_data.values())
            else:
                player_lists = []

            for players in player_lists:
                if not isinstance(players, list):
                    continue
                for p in players:
                    name = p.get("name", p.get("player", ""))
                    if not name:
                        continue
                    rating_str = p.get("rating", p.get("average_combat_score", "0"))
                    try:
                        rating_val = float(str(rating_str).replace(",", "."))
                    except (ValueError, TypeError):
                        rating_val = 0.0

                    if name not in all_players:
                        all_players[name] = {
                            "ratings": [],
                            "kills": 0,
                            "deaths": 0,
                            "assists": 0,
                        }
                    all_players[name]["ratings"].append(rating_val)
                    try:
                        all_players[name]["kills"] += int(p.get("kills", 0))
                        all_players[name]["deaths"] += int(p.get("deaths", 0))
                        all_players[name]["assists"] += int(p.get("assists", 0))
                    except (ValueError, TypeError):
                        pass

        if not all_players:
            return None

        sorted_players = sorted(
            all_players.items(),
            key=lambda x: sum(x[1]["ratings"]) / len(x[1]["ratings"]) if x[1]["ratings"] else 0,
            reverse=True,
        )

        lines = []
        for name, stats in sorted_players[:3]:
            avg_rating = sum(stats["ratings"]) / len(stats["ratings"]) if stats["ratings"] else 0
            k, d, a = stats["kills"], stats["deaths"], stats["assists"]
            lines.append(f"**{name}** — {avg_rating:.1f} rating | {k}/{d}/{a} K/D/A")

        return "\n".join(lines) if lines else None

    # ── Mongo persistence ─────────────────────────────────────────────────────

    def _live_col(self, server_id: str):
        from ._common import live_col
        return live_col(server_id)

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
            await self._live_col(server_id).replace_one(
                {"_id": match_id},
                {
                    "_id": match_id,
                    "team_name": team_name,
                    "channel_id": channel_id,
                    "message_id": message_id,
                    "match_data": match_data,
                },
                upsert=True,
            )
        except Exception as e:
            logger.warning(f"Impossible de persister le match live {match_id}: {e}")

    async def _delete_live_match(self, server_id: str, match_id: str) -> None:
        try:
            await self._live_col(server_id).delete_one({"_id": match_id})
        except Exception as e:
            logger.warning(f"Impossible de supprimer le match live persisté {match_id}: {e}")

    async def _restore_live_state(self, server_id: str, server_state: Any) -> None:
        """Restaure les matchs live depuis MongoDB après un redémarrage."""
        try:
            docs = await self._live_col(server_id).find({}).to_list(length=None)
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

    # ── Fetch match details (for ended matches) ───────────────────────────────

    async def _fetch_match_details_safe(self, match_url: str) -> dict[str, Any] | None:
        """Tente de récupérer les détails d'un match depuis son URL, renvoie None si échec."""
        vlr_match_id = extract_match_id_from_url(match_url)
        if not vlr_match_id:
            return None
        try:
            return await fetch_match_details(vlr_match_id)
        except Exception as e:
            logger.warning(f"Impossible de récupérer les détails du match {vlr_match_id}: {e}")
            return None
