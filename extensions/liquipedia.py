"""Extension Liquipedia pour le suivi des matchs esports.

Cette extension permet de suivre les matchs Valorant et WoW MDI
via l'API Liquipedia et Raider.io, avec l'API VLR.gg comme failover.
La source avec les données les plus récentes est automatiquement sélectionnée.
"""

import asyncio
from typing import List, Dict, Any, Tuple, Optional
from dataclasses import dataclass
from enum import Enum
from interactions import (
    Task,
    IntervalTrigger,
    Extension,
    listen,
    Embed,
    Client,
    TimestampStyles,
    Timestamp,
)
from interactions.client.utils import timestamp_converter
from src import logutil
from src.raiderio import get_table_data, ensure_six_elements
from src.utils import load_config, fetch
from src.vlrgg import (
    fetch_all_team_data as vlrgg_fetch_all,
    parse_vlrgg_timestamp,
    get_most_recent_result_time,
)
from datetime import datetime, timedelta

logger = logutil.init_logger(__name__)
config, module_config, enabled_servers = load_config("moduleLiquipedia")
module_config = module_config[enabled_servers[0]]

# Constants
API_KEY = config["liquipedia"]["liquipediaApiKey"]
LIQUIPEDIA_API_URL = "https://api.liquipedia.net/api/v3"
DEFAULT_EMBED_COLOR = 0xE04747
LIVE_EMBED_COLOR = 0x00FF00  # Vert pour les matchs en direct
MAX_PAST_MATCHES = 6
MAX_UPCOMING_MATCHES = 6
SCHEDULE_INTERVAL_MINUTES = 5
LIVE_UPDATE_INTERVAL_MINUTES = 1  # Mise à jour plus fréquente pour les matchs en cours
MATCH_HISTORY_WEEKS = 7


class DataSource(Enum):
    """Source des données de matchs."""
    LIQUIPEDIA = "liquipedia"
    VLRGG = "vlrgg"


class MatchStatus(Enum):
    """Statut d'un match."""
    PAST = "past"
    ONGOING = "ongoing"
    UPCOMING = "upcoming"


@dataclass
class MatchResult:
    """Résultat d'un match."""
    name: str
    value: str
    inline: bool = True


class Liquipedia(Extension):
    """Extension pour le suivi des matchs esports via Liquipedia.
    
    Attributes:
        bot: Instance du client Discord.
        message: Message Discord pour les mises à jour Valorant.
        wow_message: Message Discord pour les mises à jour WoW.
    """

    def __init__(self, bot: Client) -> None:
        self.bot = bot
        self.message = None
        self.wow_message = None
        self._headers = {"Authorization": f"Apikey {API_KEY}"}
        # Tracking des matchs en cours
        self._ongoing_matches: Dict[str, Any] = {}  # match_id -> match data
        self._live_messages: Dict[str, Any] = {}  # match_id -> discord message
        self._channel = None  # Canal pour les notifications

    @listen()
    async def on_startup(self) -> None:
        """Initialise les messages et démarre les tâches planifiées."""
        try:
            await self._initialize_messages()
            # Décommenter pour activer les tâches planifiées
            self.schedule.start()
            self.live_update.start()
            # self.mdi_schedule.start()
        except Exception as e:
            logger.error(f"Erreur lors de l'initialisation: {e}")

    async def _initialize_messages(self) -> None:
        """Récupère les messages Discord à mettre à jour."""
        self._channel = await self.bot.fetch_channel(module_config["liquipediaChannelId"])
        if self._channel and hasattr(self._channel, 'fetch_message'):
            self.message = await self._channel.fetch_message(module_config["liquipediaMessageId"])
        
        wow_channel = await self.bot.fetch_channel(module_config["liquipediaWowChannelId"])
        if wow_channel and hasattr(wow_channel, 'fetch_message'):
            self.wow_message = await wow_channel.fetch_message(module_config["liquipediaWowMessageId"])

    @Task.create(IntervalTrigger(minutes=SCHEDULE_INTERVAL_MINUTES))
    async def schedule(self) -> None:
        """Tâche planifiée pour mettre à jour les matchs Valorant."""
        logger.debug("Exécution de la tâche Liquipedia schedule")
        try:
            team = "Mandatory"
            embeds, ongoing_matches = await self._fetch_team_schedule_with_tracking(team)
            
            # Détecter les nouveaux matchs en cours
            await self._handle_match_transitions(ongoing_matches, team)
            
            if self.message and embeds:
                await self.message.edit(embeds=embeds)
            else:
                logger.warning("Aucun embed à afficher ou message non initialisé")
        except Exception as e:
            logger.exception(f"Erreur dans la tâche schedule: {e}")

    @Task.create(IntervalTrigger(minutes=LIVE_UPDATE_INTERVAL_MINUTES))
    async def live_update(self) -> None:
        """Tâche planifiée pour mettre à jour les scores des matchs en cours."""
        if not self._ongoing_matches:
            return
        
        logger.debug(f"Mise à jour live de {len(self._ongoing_matches)} match(s) en cours")
        
        try:
            team = "Mandatory"
            
            # Tenter la mise à jour via Liquipedia d'abord
            lp_success = await self._live_update_liquipedia(team)
            
            if not lp_success:
                # Fallback VLR.gg pour les mises à jour live
                logger.info("Basculement sur VLR.gg pour la mise à jour live")
                await self._live_update_vlrgg(team)
                
        except Exception as e:
            logger.exception(f"Erreur dans live_update: {e}")

    async def _live_update_liquipedia(self, team: str) -> bool:
        """Met à jour les scores live via Liquipedia.
        
        Args:
            team: Nom de l'équipe.
            
        Returns:
            True si la mise à jour a réussi, False sinon.
        """
        try:
            date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            data = await self.liquipedia_request(
                "valorant",
                "match",
                f"[[opponent::{team}]] AND [[date::>{date}]]",
                limit=5,
                order="date DESC",
            )
            
            matches_to_remove = []
            
            for match in data.get("result", []):
                match_id = match.get("match2id") or match.get("pagename", "")
                
                if match_id in self._ongoing_matches:
                    if match.get("finished") == 1:
                        await self._handle_match_ended(match, match_id, team)
                        matches_to_remove.append(match_id)
                    else:
                        await self._update_live_message(match, match_id)
            
            for match_id in matches_to_remove:
                self._ongoing_matches.pop(match_id, None)
            
            return True
        except Exception as e:
            logger.warning(f"Échec de la mise à jour live via Liquipedia: {e}")
            return False

    async def _live_update_vlrgg(self, team: str) -> None:
        """Met à jour les scores live via VLR.gg (fallback).
        
        Args:
            team: Nom de l'équipe.
        """
        try:
            vlr_data = await self._fetch_vlrgg_data(team)
            if not vlr_data:
                return
            
            vlr_live = vlr_data.get("live", [])
            
            for match_id in list(self._ongoing_matches.keys()):
                # Pour les matchs VLR.gg, vérifier s'ils sont toujours live
                if match_id.startswith("vlrgg_"):
                    still_live = any(
                        f"vlrgg_{m.get('team1', '')}_{m.get('team2', '')}" == match_id
                        for m in vlr_live
                    )
                    if not still_live:
                        # Match probablement terminé
                        message = self._live_messages.pop(match_id, None)
                        if message:
                            try:
                                await message.edit(
                                    content="**Match terminé!**",
                                )
                            except Exception:
                                pass
                        self._ongoing_matches.pop(match_id, None)
                    else:
                        # Mettre à jour le score
                        for m in vlr_live:
                            vlr_id = f"vlrgg_{m.get('team1', '')}_{m.get('team2', '')}"
                            if vlr_id == match_id:
                                await self._update_vlrgg_live_message(m, match_id)
                                break
        except Exception as e:
            logger.warning(f"Échec de la mise à jour live via VLR.gg: {e}")

    async def _update_vlrgg_live_message(
        self, match: Dict[str, Any], match_id: str
    ) -> None:
        """Met à jour le message de score en direct depuis VLR.gg.
        
        Args:
            match: Données du match VLR.gg.
            match_id: Identifiant du match.
        """
        message = self._live_messages.get(match_id)
        if not message:
            return
        
        try:
            team1 = match.get("team1", "???")
            team2 = match.get("team2", "???")
            score1 = match.get("score1", "?")
            score2 = match.get("score2", "?")
            current_map = match.get("current_map", "")
            event = match.get("match_event", "")
            
            embed = Embed(
                title=f"🔴 LIVE: {team1} vs {team2}",
                description=f"**{event}**",
                color=LIVE_EMBED_COLOR,
                timestamp=Timestamp.now(),
            )
            
            embed.add_field(
                name="Score",
                value=f"**{team1}** {score1} - {score2} **{team2}**",
                inline=False
            )
            
            if current_map:
                embed.add_field(
                    name="Map actuelle",
                    value=current_map,
                    inline=False
                )
            
            # Détails des rounds
            t1_ct = match.get("team1_round_ct", "")
            t1_t = match.get("team1_round_t", "")
            t2_ct = match.get("team2_round_ct", "")
            t2_t = match.get("team2_round_t", "")
            if t1_ct and t1_ct != "N/A":
                embed.add_field(
                    name="Rounds",
                    value=f"CT: {t1_ct}-{t2_ct} | T: {t1_t}-{t2_t}",
                    inline=False
                )
            
            embed.set_footer(text="Mise à jour automatique • Source: VLR.gg")
            
            await message.edit(embeds=[embed])
            logger.debug(f"Message live VLR.gg mis à jour pour {match_id}: {score1}-{score2}")
            
        except Exception as e:
            logger.exception(f"Erreur lors de la mise à jour VLR.gg du message live: {e}")

    async def _handle_match_transitions(self, ongoing_matches: Dict[str, Any], team: str) -> None:
        """Gère les transitions de matchs (début/fin).
        
        Supporte les matchs provenant de Liquipedia et VLR.gg.
        
        Args:
            ongoing_matches: Dictionnaire des matchs actuellement en cours.
            team: Nom de l'équipe suivie.
        """
        # Détecter les nouveaux matchs
        for match_id, match in ongoing_matches.items():
            if match_id not in self._ongoing_matches:
                logger.info(f"Nouveau match détecté: {match_id}")
                if match_id.startswith("vlrgg_"):
                    await self._send_vlrgg_match_started_notification(match, team)
                else:
                    await self._send_match_started_notification(match, team)
                self._ongoing_matches[match_id] = match
        
        # Détecter les matchs terminés (qui ne sont plus en cours)
        finished_matches = [
            mid for mid in self._ongoing_matches 
            if mid not in ongoing_matches
        ]
        for match_id in finished_matches:
            logger.info(f"Match terminé détecté: {match_id}")
            self._ongoing_matches.pop(match_id, None)
            # Le message de fin sera géré par live_update

    async def _send_match_started_notification(self, match: Dict[str, Any], team: str) -> None:
        """Envoie une notification quand un match commence.
        
        Args:
            match: Données du match.
            team: Nom de l'équipe suivie.
        """
        if not self._channel:
            logger.warning("Canal non initialisé pour les notifications")
            return
        
        try:
            name_1, name_2, shortname_1, shortname_2 = self._get_match_teams(match)
            score_1, score_2 = self._calculate_match_scores(match)
            
            embed = Embed(
                title=f"🔴 LIVE: {name_1} vs {name_2}",
                description=(
                    f"**{match.get('tickername', 'Tournoi')}**\n\n"
                    f"Bo{match.get('bestof', '?')}\n"
                    f"Le match vient de commencer!"
                ),
                color=LIVE_EMBED_COLOR,
                timestamp=Timestamp.now(),
            )
            
            # Score actuel
            embed.add_field(
                name="Score",
                value=f"**{shortname_1}** {score_1} - {score_2} **{shortname_2}**",
                inline=False
            )
            
            embed.set_footer(text="Mise à jour automatique • Source: Liquipedia")
            
            # Envoyer le message et le stocker
            match_id = match.get("match2id") or match.get("pagename", "")
            message = await self._channel.send(
                content="<:zrtON:962320783038890054> **Match en direct!** <:zrtON:962320783038890054>",
                embeds=[embed]
            )
            self._live_messages[match_id] = message
            logger.info(f"Notification envoyée pour le match {name_1} vs {name_2}")
            
        except Exception as e:
            logger.exception(f"Erreur lors de l'envoi de la notification: {e}")

    async def _send_vlrgg_match_started_notification(
        self, match: Dict[str, Any], team: str
    ) -> None:
        """Envoie une notification quand un match commence (source VLR.gg).
        
        Args:
            match: Données du match VLR.gg.
            team: Nom de l'équipe suivie.
        """
        if not self._channel:
            logger.warning("Canal non initialisé pour les notifications")
            return
        
        try:
            team1 = match.get("team1", "???")
            team2 = match.get("team2", "???")
            score1 = match.get("score1", "0")
            score2 = match.get("score2", "0")
            event = match.get("match_event", "Tournoi")
            
            embed = Embed(
                title=f"🔴 LIVE: {team1} vs {team2}",
                description=(
                    f"**{event}**\n\n"
                    f"Le match vient de commencer!"
                ),
                color=LIVE_EMBED_COLOR,
                timestamp=Timestamp.now(),
            )
            
            embed.add_field(
                name="Score",
                value=f"**{team1}** {score1} - {score2} **{team2}**",
                inline=False
            )
            
            embed.set_footer(text="Mise à jour automatique • Source: VLR.gg")
            
            match_id = f"vlrgg_{team1}_{team2}"
            message = await self._channel.send(
                content="<:zrtON:962320783038890054> **Match en direct!** <:zrtON:962320783038890054>",
                embeds=[embed]
            )
            self._live_messages[match_id] = message
            logger.info(f"Notification VLR.gg envoyée pour le match {team1} vs {team2}")
            
        except Exception as e:
            logger.exception(f"Erreur lors de l'envoi de la notification VLR.gg: {e}")

    async def _update_live_message(self, match: Dict[str, Any], match_id: str) -> None:
        """Met à jour le message de score en direct.
        
        Args:
            match: Données actuelles du match.
            match_id: Identifiant du match.
        """
        message = self._live_messages.get(match_id)
        if not message:
            return
        
        try:
            name_1, name_2, shortname_1, shortname_2 = self._get_match_teams(match)
            score_1, score_2 = self._calculate_match_scores(match)
            
            embed = Embed(
                title=f"🔴 LIVE: {name_1} vs {name_2}",
                description=(
                    f"**{match.get('tickername', 'Tournoi')}**\n\n"
                    f"Bo{match.get('bestof', '?')}"
                ),
                color=LIVE_EMBED_COLOR,
                timestamp=Timestamp.now(),
            )
            
            # Score actuel
            embed.add_field(
                name="Score",
                value=f"**{shortname_1}** {score_1} - {score_2} **{shortname_2}**",
                inline=False
            )
            
            # Détails des maps jouées
            map_veto = match["extradata"].get("mapveto", {})
            games_details = self._format_live_games(match["match2games"], map_veto, shortname_1, shortname_2)
            if games_details:
                embed.add_field(
                    name="Maps",
                    value=games_details,
                    inline=False
                )
            
            embed.set_footer(text="Mise à jour automatique • Source: Liquipedia")
            
            await message.edit(embeds=[embed])
            logger.debug(f"Message live mis à jour pour {match_id}: {score_1}-{score_2}")
            
        except Exception as e:
            logger.exception(f"Erreur lors de la mise à jour du message live: {e}")

    def _format_live_games(self, games: List[Dict], map_veto: Dict, shortname_1: str, shortname_2: str) -> str | None:
        """Formate les maps pour l'affichage live."""
        result = ""
        for game in games:
            if game.get("resulttype") == "np":
                continue
            
            map_name = game.get("map", "???")
            scores = game.get("scores", [0, 0])
            
            if scores and scores[0] is not None and scores[1] is not None:
                game_result = self._format_game_score(int(scores[0]), int(scores[1]))
                winner_emoji = ""
                if game.get("winner") == "1":
                    winner_emoji = " ✅"
                elif game.get("winner") == "2":
                    winner_emoji = " ❌"
                result += f"**{map_name}**: {shortname_1} {game_result} {shortname_2}{winner_emoji}\n"
        
        return result if result else None

    async def _handle_match_ended(self, match: Dict[str, Any], match_id: str, team: str) -> None:
        """Gère la fin d'un match.
        
        Args:
            match: Données du match.
            match_id: Identifiant du match.
            team: Nom de l'équipe suivie.
        """
        message = self._live_messages.pop(match_id, None)
        if not message:
            return
        
        try:
            name_1, name_2, shortname_1, shortname_2 = self._get_match_teams(match)
            score_1, score_2 = self._calculate_match_scores(match)
            
            winner = int(match.get("winner", 0)) - 1
            winner_name = match["match2opponents"][winner]["name"] if winner >= 0 else "???"
            
            is_victory = winner_name == team
            result_emoji = "🎉" if is_victory else "😢"
            result_text = "VICTOIRE" if is_victory else "DÉFAITE"
            embed_color = 0x00FF00 if is_victory else 0xFF0000
            
            embed = Embed(
                title=f"{result_emoji} {result_text}: {name_1} vs {name_2}",
                description=(
                    f"**{match.get('tickername', 'Tournoi')}**\n\n"
                    f"Bo{match.get('bestof', '?')} terminé!"
                ),
                color=embed_color,
                timestamp=Timestamp.now(),
            )
            
            # Score final
            embed.add_field(
                name="Score Final",
                value=f"**{shortname_1}** {score_1} - {score_2} **{shortname_2}**",
                inline=False
            )
            
            # Détails des maps
            map_veto = match["extradata"].get("mapveto", {})
            games_details = self._format_games_list(match["match2games"], map_veto, shortname_1, shortname_2)
            if games_details:
                embed.add_field(
                    name="Détail des maps",
                    value=games_details,
                    inline=False
                )
            
            embed.set_footer(text="Match terminé • Source: Liquipedia")
            
            await message.edit(
                content=f"{result_emoji} **Match terminé!** {result_emoji}",
                embeds=[embed]
            )
            logger.info(f"Match terminé: {name_1} {score_1}-{score_2} {name_2}")
            
        except Exception as e:
            logger.exception(f"Erreur lors de la gestion de fin de match: {e}")

    async def _fetch_team_schedule_with_tracking(
        self, team: str
    ) -> Tuple[List[Embed], Dict[str, Any]]:
        """Récupère le planning d'une équipe avec suivi des matchs en cours.
        
        Utilise Liquipedia comme source principale et VLR.gg comme failover.
        Compare la fraîcheur des données pour choisir la meilleure source.
        
        Args:
            team: Nom de l'équipe à suivre.
            
        Returns:
            Tuple (liste des embeds, dictionnaire des matchs en cours).
        """
        # Récupérer les données des deux sources en parallèle
        lp_task = self._fetch_liquipedia_data(team)
        vlr_task = self._fetch_vlrgg_data(team)
        
        lp_result, vlr_result = await asyncio.gather(
            lp_task, vlr_task, return_exceptions=True
        )
        
        lp_ok = not isinstance(lp_result, Exception) and lp_result is not None
        vlr_ok = not isinstance(vlr_result, Exception) and vlr_result is not None
        
        # Choisir la meilleure source
        source = DataSource.LIQUIPEDIA
        if lp_ok and vlr_ok:
            assert isinstance(lp_result, dict) and isinstance(vlr_result, dict)
            if self._vlrgg_has_fresher_data(lp_result, vlr_result):
                source = DataSource.VLRGG
                logger.info("VLR.gg sélectionné (données plus récentes)")
            else:
                logger.info("Liquipedia sélectionné (données plus récentes ou équivalentes)")
        elif not lp_ok and vlr_ok:
            source = DataSource.VLRGG
            logger.warning(f"Liquipedia indisponible, basculement sur VLR.gg: {lp_result}")
        elif not lp_ok and not vlr_ok:
            logger.error(f"Les deux sources ont échoué: LP={lp_result}, VLR={vlr_result}")
            raise Exception("Les deux sources de données (Liquipedia et VLR.gg) sont indisponibles")
        
        if source == DataSource.VLRGG:
            assert isinstance(vlr_result, dict)
            embeds = self._build_vlrgg_embeds(vlr_result, team)
            ongoing = self._extract_vlrgg_ongoing(vlr_result)
            return embeds, ongoing
        else:
            # Source Liquipedia — flux existant
            assert isinstance(lp_result, dict)
            embeds, pagenames = await self.make_schedule_embed(lp_result, team)
            
            # Extraire les matchs en cours
            ongoing_matches: Dict[str, Any] = {}
            current_time = datetime.now().timestamp()
            
            for match in lp_result.get("result", []):
                match_timestamp = match["extradata"].get("timestamp", 0)
                if match_timestamp < current_time and match.get("finished") == 0:
                    match_id = match.get("match2id") or match.get("pagename", "")
                    ongoing_matches[match_id] = match
            
            # Ajouter les classements des tournois
            for pagename in pagenames:
                standings_embeds = await self._fetch_tournament_standings(pagename)
                embeds.extend(standings_embeds)
            
            return embeds, ongoing_matches

    async def _fetch_liquipedia_data(self, team: str) -> Dict[str, Any]:
        """Récupère les données Liquipedia pour une équipe.
        
        Args:
            team: Nom de l'équipe.
            
        Returns:
            Données JSON de l'API Liquipedia.
        """
        date = (datetime.now() - timedelta(weeks=MATCH_HISTORY_WEEKS)).strftime("%Y-%m-%d")
        return await self.liquipedia_request(
            "valorant",
            "match",
            f"[[opponent::{team}]] AND [[date::>{date}]]",
            limit=15,
            order="date ASC",
        )

    async def _fetch_vlrgg_data(self, team: str) -> Optional[Dict[str, Any]]:
        """Récupère les données VLR.gg pour une équipe.
        
        Args:
            team: Nom de l'équipe.
            
        Returns:
            Dictionnaire avec les clés 'results', 'upcoming', 'live'.
        """
        try:
            data = await vlrgg_fetch_all(team)
            total = len(data.get("results", [])) + len(data.get("upcoming", [])) + len(data.get("live", []))
            if total == 0:
                logger.debug(f"VLR.gg: aucune donnée trouvée pour {team}")
                return None
            return data
        except Exception as e:
            logger.error(f"Erreur lors de la récupération VLR.gg: {e}")
            return None

    def _vlrgg_has_fresher_data(
        self, lp_data: Dict[str, Any], vlr_data: Dict[str, Any]
    ) -> bool:
        """Détermine si VLR.gg a des données plus fraîches que Liquipedia.
        
        Critères:
        - VLR.gg a un match en direct que Liquipedia ne détecte pas
        - VLR.gg a un résultat plus récent que le dernier de Liquipedia
        
        Args:
            lp_data: Données Liquipedia (résultat de liquipedia_request).
            vlr_data: Données VLR.gg (résultat de fetch_all_team_data).
            
        Returns:
            True si VLR.gg est plus frais.
        """
        # Si VLR.gg a des matchs live et Liquipedia n'en détecte pas
        vlr_live = vlr_data.get("live", [])
        current_time = datetime.now().timestamp()
        
        lp_ongoing_count = sum(
            1 for m in lp_data.get("result", [])
            if m.get("finished") == 0
            and m.get("extradata", {}).get("timestamp", 0) < current_time
        )
        
        if vlr_live and lp_ongoing_count == 0:
            logger.info("VLR.gg détecte un match live que Liquipedia ne montre pas")
            return True
        
        # Comparer le résultat le plus récent
        vlr_results = vlr_data.get("results", [])
        most_recent_vlr = get_most_recent_result_time(vlr_results)
        
        if most_recent_vlr:
            # Trouver le timestamp du dernier résultat Liquipedia
            lp_finished = [
                m for m in lp_data.get("result", [])
                if m.get("finished") == 1
            ]
            if lp_finished:
                latest_lp_ts = max(
                    m.get("extradata", {}).get("timestamp", 0) for m in lp_finished
                )
                latest_lp = datetime.fromtimestamp(latest_lp_ts) if latest_lp_ts else None
                
                if latest_lp and most_recent_vlr > latest_lp:
                    logger.info(
                        f"VLR.gg a un résultat plus récent: {most_recent_vlr} vs {latest_lp}"
                    )
                    return True
            elif vlr_results:
                # Liquipedia n'a aucun résultat mais VLR.gg en a
                return True
        
        # Comparer les matchs à venir
        vlr_upcoming = vlr_data.get("upcoming", [])
        lp_upcoming = [
            m for m in lp_data.get("result", [])
            if m.get("extradata", {}).get("timestamp", 0) > current_time
        ]
        
        if vlr_upcoming and not lp_upcoming:
            logger.info("VLR.gg a des matchs à venir que Liquipedia ne montre pas")
            return True
        
        return False

    def _build_vlrgg_embeds(
        self, vlr_data: Dict[str, Any], team: str
    ) -> List[Embed]:
        """Construit les embeds Discord à partir des données VLR.gg.
        
        Args:
            vlr_data: Données VLR.gg.
            team: Nom de l'équipe.
            
        Returns:
            Liste des embeds à afficher.
        """
        embeds = []
        
        # Embed des résultats passés
        results = vlr_data.get("results", [])
        if results:
            past_embed = self._create_base_embed(
                f"Derniers matchs de {team}",
                footer_text="Source: VLR.gg (failover)"
            )
            for i, match in enumerate(results[:MAX_PAST_MATCHES]):
                field = self._format_vlrgg_result(match, team)
                past_embed.add_field(
                    name=field["name"], value=field["value"], inline=True
                )
                if (i + 1) % 2 != 0:
                    past_embed.add_field(name="\u200b", value="\u200b", inline=True)
            embeds.append(past_embed)
        
        # Embed des matchs en direct
        live = vlr_data.get("live", [])
        if live:
            live_embed = self._create_base_embed(
                f"Match en cours de {team}",
                footer_text="Source: VLR.gg (failover)"
            )
            for match in live:
                field = self._format_vlrgg_live(match)
                live_embed.add_field(
                    name=field["name"], value=field["value"], inline=False
                )
            embeds.append(live_embed)
        
        # Embed des matchs à venir
        upcoming = vlr_data.get("upcoming", [])
        if upcoming:
            upcoming_embed = self._create_base_embed(
                f"Prochains matchs de {team}",
                footer_text="Source: VLR.gg (failover)"
            )
            for i, match in enumerate(upcoming[:MAX_UPCOMING_MATCHES]):
                field = self._format_vlrgg_upcoming(match)
                upcoming_embed.add_field(
                    name=field["name"], value=field["value"], inline=True
                )
                if (i + 1) % 2 != 0:
                    upcoming_embed.add_field(name="\u200b", value="\u200b", inline=True)
            embeds.append(upcoming_embed)
        
        return embeds

    def _format_vlrgg_result(self, match: Dict[str, Any], team: str) -> Dict[str, str]:
        """Formate un résultat VLR.gg pour l'affichage."""
        team1 = match.get("team1", "???")
        team2 = match.get("team2", "???")
        score1 = match.get("score1", "?")
        score2 = match.get("score2", "?")
        time_completed = match.get("time_completed", "?")
        tournament = match.get("tournament_name", match.get("round_info", ""))
        
        # Déterminer victoire/défaite
        try:
            s1, s2 = int(score1), int(score2)
            team_lower = team.lower()
            is_team1 = team_lower in team1.lower()
            team_won = (is_team1 and s1 > s2) or (not is_team1 and s2 > s1)
            resultat = (
                "Gagné <:zrtHypers:1257757857122877612>"
                if team_won
                else "Perdu <:zrtCry:1257757854861885571>"
            )
        except (ValueError, TypeError):
            resultat = ""
        
        return {
            "name": f"{team1} {score1}-{score2} {team2}",
            "value": f"{tournament}\n{time_completed}\n{resultat}",
        }

    def _format_vlrgg_live(self, match: Dict[str, Any]) -> Dict[str, str]:
        """Formate un match live VLR.gg pour l'affichage."""
        team1 = match.get("team1", "???")
        team2 = match.get("team2", "???")
        score1 = match.get("score1", "?")
        score2 = match.get("score2", "?")
        current_map = match.get("current_map", "")
        event = match.get("match_event", match.get("match_series", ""))
        
        value = f"**{event}**\n"
        if current_map:
            value += f"Map actuelle: {current_map}\n"
        
        # Détails des rounds si disponibles
        t1_ct = match.get("team1_round_ct", "")
        t1_t = match.get("team1_round_t", "")
        t2_ct = match.get("team2_round_ct", "")
        t2_t = match.get("team2_round_t", "")
        if t1_ct and t1_ct != "N/A":
            value += f"Rounds — CT: {t1_ct}-{t2_ct} | T: {t1_t}-{t2_t}"
        
        return {
            "name": f"<:zrtON:962320783038890054> {team1} {score1}-{score2} {team2} <:zrtON:962320783038890054>",
            "value": value,
        }

    def _format_vlrgg_upcoming(self, match: Dict[str, Any]) -> Dict[str, str]:
        """Formate un match à venir VLR.gg pour l'affichage."""
        team1 = match.get("team1", "???")
        team2 = match.get("team2", "???")
        eta = match.get("time_until_match", "?")
        event = match.get("match_event", match.get("match_series", ""))
        timestamp_str = match.get("unix_timestamp", "")
        
        time_display = eta
        ts = parse_vlrgg_timestamp(timestamp_str)
        if ts:
            time_display = f"<t:{int(ts.timestamp())}:R>"
        
        return {
            "name": f"{team1} vs {team2}",
            "value": f"{time_display}\n{event}",
        }

    def _extract_vlrgg_ongoing(self, vlr_data: Dict[str, Any]) -> Dict[str, Any]:
        """Extrait les matchs en cours depuis les données VLR.gg.
        
        Args:
            vlr_data: Données VLR.gg.
            
        Returns:
            Dictionnaire match_id -> match data.
        """
        ongoing: Dict[str, Any] = {}
        for match in vlr_data.get("live", []):
            match_id = f"vlrgg_{match.get('team1', '')}_{match.get('team2', '')}"
            ongoing[match_id] = match
        return ongoing

    async def _fetch_team_schedule(self, team: str) -> List[Embed]:
        """Récupère le planning d'une équipe avec failover VLR.gg.
        
        Args:
            team: Nom de l'équipe à suivre.
            
        Returns:
            Liste des embeds à afficher.
        """
        # Tenter les deux sources en parallèle
        lp_task = self._fetch_liquipedia_data(team)
        vlr_task = self._fetch_vlrgg_data(team)
        
        lp_result, vlr_result = await asyncio.gather(
            lp_task, vlr_task, return_exceptions=True
        )
        
        lp_ok = not isinstance(lp_result, Exception) and lp_result is not None
        vlr_ok = not isinstance(vlr_result, Exception) and vlr_result is not None
        
        source = DataSource.LIQUIPEDIA
        if lp_ok and vlr_ok:
            assert isinstance(lp_result, dict) and isinstance(vlr_result, dict)
            if self._vlrgg_has_fresher_data(lp_result, vlr_result):
                source = DataSource.VLRGG
        elif not lp_ok and vlr_ok:
            source = DataSource.VLRGG
        elif not lp_ok and not vlr_ok:
            raise Exception("Les deux sources de données sont indisponibles")
        
        if source == DataSource.VLRGG:
            assert isinstance(vlr_result, dict)
            return self._build_vlrgg_embeds(vlr_result, team)
        
        assert isinstance(lp_result, dict)
        embeds, pagenames = await self.make_schedule_embed(lp_result, team)
        
        # Ajouter les classements des tournois
        for pagename in pagenames:
            standings_embeds = await self._fetch_tournament_standings(pagename)
            embeds.extend(standings_embeds)
        
        return embeds

    async def _fetch_tournament_standings(self, pagename: str) -> List[Embed]:
        """Récupère les classements d'un tournoi.
        
        Args:
            pagename: Identifiant de la page du tournoi.
            
        Returns:
            Liste des embeds de classement.
        """
        embeds = []
        tournament = await self.liquipedia_request(
            "valorant",
            "tournament",
            f"[[pagename::{pagename}]]",
            query="participantsnumber, name",
        )
        
        if not tournament.get("result"):
            return embeds
            
        participants_number = int(tournament["result"][0]["participantsnumber"])
        tournament_name = tournament["result"][0]["name"]
        
        standings = await self.liquipedia_request(
            "valorant",
            "standingsentry",
            f"[[parent::{pagename}]]",
            limit=participants_number * 2,
            order="roundindex DESC",
        )
        
        clean_standings = await self.organize_standings(standings)
        for pageid in clean_standings:
            embeds.append(
                await self.make_standings_embed(
                    clean_standings[pageid], f"Classement de {tournament_name}"
                )
            )
        return embeds

    async def liquipedia_request(
        self,
        wiki: str,
        datapoint: str,
        conditions: str = "",
        query: str = "",
        limit: int | str = "",
        offset: int | str = "",
        order: str = "",
    ) -> Dict[str, Any]:
        """Effectue une requête vers l'API Liquipedia.
        
        Args:
            wiki: Nom du wiki (valorant, worldofwarcraft, etc.).
            datapoint: Type de données (match, tournament, etc.).
            conditions: Conditions de filtrage.
            query: Champs à récupérer.
            limit: Nombre maximum de résultats.
            offset: Décalage pour la pagination.
            order: Ordre de tri.
            
        Returns:
            Données JSON de la réponse.
            
        Raises:
            Exception: En cas d'erreur de requête.
        """
        params = {
            key: value
            for key, value in {
                "wiki": wiki,
                "conditions": conditions,
                "query": query,
                "limit": limit,
                "offset": offset,
                "order": order,
            }.items()
            if value  # Filtrer les valeurs vides
        }
        url = f"{LIQUIPEDIA_API_URL}/{datapoint}"
        logger.debug(f"Requête Liquipedia: {url} | params: {params}")
        return await fetch(url, headers=self._headers, params=params, return_type="json")

    async def organize_standings(
        self, data: Dict[str, Any]
    ) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
        """Organise les classements par page et par semaine.
        
        Args:
            data: Données brutes de l'API.
            
        Returns:
            Dictionnaire organisé par pageid -> roundindex -> liste des équipes.
        """
        organized: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        
        for entry in data.get("result", []):
            pageid = entry["pageid"]
            roundindex = entry["roundindex"]
            scoreboard = entry.get("scoreboard", {})
            diff = scoreboard.get("diff", 0)
            
            team_data = {
                "team": entry["opponentname"],
                "standing": entry["placement"],
                "match": self._extract_score(scoreboard.get("match", {})),
                "game": self._extract_score(scoreboard.get("game", {})),
                "diff_rounds": f"+{diff}" if diff > 0 else str(diff),
                "placementchange": entry.get("placementchange", 0),
                "currentstatus": entry.get("currentstatus", ""),
                "definitestatus": entry.get("definitestatus", ""),
            }
            
            organized.setdefault(pageid, {}).setdefault(roundindex, []).append(team_data)

        # Trier par classement
        for pageid in organized:
            for roundindex in organized[pageid]:
                organized[pageid][roundindex].sort(key=lambda e: e["standing"])
        
        return organized

    @staticmethod
    def _extract_score(score_data: Dict[str, Any]) -> Dict[str, int]:
        """Extrait les scores win/loss/draw."""
        return {
            "win": score_data.get("w", 0),
            "loss": score_data.get("l", 0),
            "draw": score_data.get("d", 0),
        }

    async def make_standings_embed(
        self, data: Dict[str, List[Dict[str, Any]]], name: str = "Classement"
    ) -> Embed:
        """Crée un embed de classement.
        
        Args:
            data: Données de classement par semaine.
            name: Titre de l'embed.
            
        Returns:
            Embed Discord formaté.
        """
        embed = self._create_base_embed(name, footer_text="Source: Liquipedia")

        for week, standings in data.items():
            formatted_lines = [
                self._format_standing_line(team) for team in standings
            ]
            field_value = f"```ansi\n{''.join(formatted_lines)}```"
            embed.add_field(name=f"Semaine {week}", value=field_value)
        
        return embed

    def _format_standing_line(self, team: Dict[str, Any]) -> str:
        """Formate une ligne de classement."""
        diff_txt = self._format_placement_change(team["placementchange"])
        standing_str = self._format_status(team["currentstatus"], str(team["standing"]), bold=True)
        team_str = self._format_status(team["definitestatus"], f"{team['team']:<23}")
        match_record = f"({team['match']['win']}-{team['match']['loss']})"
        return f"{standing_str} {team_str} {match_record} {diff_txt} ({team['diff_rounds']})\n"

    @staticmethod
    def _format_placement_change(placement_change: int) -> str:
        """Formate le changement de placement avec couleur ANSI."""
        if placement_change > 0:
            return f"\u001b[1;32m▲{placement_change}\u001b[0m"
        elif placement_change < 0:
            return f"\u001b[1;31m▼{-placement_change}\u001b[0m"
        return "\u001b[1;30m==\u001b[0m"

    @staticmethod
    def _format_status(status: str, text: str, bold: bool = False) -> str:
        """Formate le texte avec couleur ANSI selon le statut."""
        bold_code = "1" if bold else "0"
        colors = {"up": "32", "down": "31", "stay": "33"}
        color = colors.get(status, "")
        if color:
            return f"\u001b[{bold_code};{color}m{text}\u001b[0m"
        return text

    def _create_base_embed(
        self, title: str, description: str = "", footer_text: str = ""
    ) -> Embed:
        """Crée un embed de base avec le style commun."""
        embed = Embed(
            title=title,
            description=description,
            color=DEFAULT_EMBED_COLOR,
            timestamp=Timestamp.now(),
        )
        if footer_text:
            embed.set_footer(text=footer_text)
        return embed

    def _get_match_teams(self, match: Dict[str, Any]) -> Tuple[str, str, str, str]:
        """Extrait les noms des équipes d'un match.
        
        Returns:
            Tuple (name_1, name_2, shortname_1, shortname_2)
        """
        opponents = match["match2opponents"]
        return (
            opponents[0]["name"],
            opponents[1]["name"],
            opponents[0]["teamtemplate"]["shortname"],
            opponents[1]["teamtemplate"]["shortname"],
        )

    def _get_veto_info(
        self, map_name: str, map_veto: Dict, shortname_1: str, shortname_2: str
    ) -> str:
        """Détermine l'info de veto pour une map."""
        for veto in map_veto.values():
            if veto.get("team1") == map_name:
                return f"(Pick {shortname_1})"
            elif veto.get("team2") == map_name:
                return f"(Pick {shortname_2})"
            elif veto.get("type") == "decider" and veto.get("decider") == map_name:
                return "(Decider)"
        return ""

    @staticmethod
    def _format_game_score(score_1: int, score_2: int) -> str:
        """Formate le score d'une map avec le gagnant en gras."""
        if score_1 > score_2:
            return f"**{score_1}**-{score_2}"
        elif score_2 > score_1:
            return f"{score_1}-**{score_2}**"
        return f"{score_1}-{score_2}"

    def format_past_match(
        self,
        match: Dict[str, Any],
        score_1: int,
        score_2: int,
        name: str,
    ) -> Dict[str, str]:
        """Formate un match passé pour l'affichage."""
        name_1, name_2, shortname_1, shortname_2 = self._get_match_teams(match)
        winner = int(match["winner"]) - 1
        winner_name = match["match2opponents"][winner]["name"]
        date = timestamp_converter(match["extradata"]["timestamp"])
        
        resultat = (
            "Gagné <:zrtHypers:1257757857122877612>"
            if winner_name == name
            else "Perdu <:zrtCry:1257757854861885571>"
        )
        
        map_veto = match["extradata"].get("mapveto", {})
        games = self._format_games_list(match["match2games"], map_veto, shortname_1, shortname_2)

        return {
            "name": f"{name_1} {score_1}-{score_2} {name_2} (Bo{match['bestof']})",
            "value": f"{match['tickername']}\n{date}\n{games}{resultat}",
        }

    def _format_games_list(
        self, games: List[Dict], map_veto: Dict, shortname_1: str, shortname_2: str
    ) -> str:
        """Formate la liste des games d'un match."""
        result = ""
        for game in games:
            if game.get("resulttype") == "np":
                break
            
            map_name = game["map"]
            veto_info = self._get_veto_info(map_name, map_veto, shortname_1, shortname_2)
            
            scores = game.get("scores", [0, 0])
            game_result = self._format_game_score(int(scores[0]), int(scores[1]))
            result += f"**{map_name}** : {game_result} {veto_info}\n"
        
        return result

    def format_ongoing_match(
        self,
        match: Dict[str, Any],
        score_1: int,
        score_2: int,
    ) -> List[Dict[str, str]]:
        """Formate un match en cours pour l'affichage.
        
        Returns:
            Liste de champs d'embed (header + une entrée par map).
        """
        name_1, name_2, shortname_1, shortname_2 = self._get_match_teams(match)
        
        embeds = [
            {
                "name": f"<:zrtON:962320783038890054> {name_1} {score_1}-{score_2} {name_2} en Bo{match['bestof']} <:zrtON:962320783038890054>",
                "value": f"En cours\n{match['tickername']}",
            }
        ]
        
        map_veto = match["extradata"].get("mapveto", {})
        
        for game in match["match2games"]:
            embed = self._format_ongoing_game(
                game, map_veto, name_1, name_2, shortname_1, shortname_2
            )
            embeds.append(embed)
        
        return embeds

    def _format_ongoing_game(
        self,
        game: Dict[str, Any],
        map_veto: Dict,
        name_1: str,
        name_2: str,
        shortname_1: str,
        shortname_2: str,
    ) -> Dict[str, str]:
        """Formate une game en cours."""
        map_name = game["map"]
        players_info = self._format_players_info(game, name_1, name_2)
        veto_info = self._get_veto_info(map_name, map_veto, shortname_1, shortname_2)
        
        # Format des scores si disponibles
        if game.get("resulttype") != "np" and game.get("scores"):
            scores = game["scores"]
            game_result = self._format_game_score(int(scores[0]), int(scores[1]))
            value = f"{shortname_1} {game_result} {shortname_2}"
            if players_info:
                value += f"\n{players_info}"
        else:
            value = "\u200b"
        
        return {"name": f"**{map_name}** {veto_info}", "value": value}

    def _format_players_info(self, game: Dict, name_1: str, name_2: str) -> str:
        """Formate les infos des joueurs en deux colonnes."""
        participants = game.get("participants", {})
        if not isinstance(participants, dict):
            return ""
        
        players_team1 = []
        players_team2 = []
        
        for participant in participants.values() if isinstance(participants, dict) else participants:
            if not isinstance(participant, dict):
                continue
            player_name = participant.get("player")
            agent_name = participant.get("agent")
            team = participant.get("team")
            
            if player_name and agent_name:
                player_info = f"{player_name}: {agent_name}"
                if team == name_1:
                    players_team1.append(player_info)
                elif team == name_2:
                    players_team2.append(player_info)
        
        if not players_team1 and not players_team2:
            return ""
        
        max_players = max(len(players_team1), len(players_team2))
        return "\n".join(
            f"{players_team1[i] if i < len(players_team1) else '':<30} "
            f"{players_team2[i] if i < len(players_team2) else ''}"
            for i in range(max_players)
        )

    def format_upcoming_match(self, match: Dict[str, Any]) -> Dict[str, str]:
        """Formate un match à venir pour l'affichage."""
        opponents = match["match2opponents"]
        name_1 = opponents[0]["name"]
        name_2 = opponents[1]["name"]
        timestamp = timestamp_converter(match["extradata"]["timestamp"])
        
        return {
            "name": f"{name_1} vs {name_2} (Bo{match['bestof']})",
            "value": f"{timestamp}\n{match['tickername']}",
        }

    async def make_schedule_embed(
        self, data: Dict[str, Any], name: str
    ) -> Tuple[List[Embed], List[str]]:
        """Crée les embeds de planning pour une équipe.
        
        Args:
            data: Données des matchs de l'API.
            name: Nom de l'équipe.
            
        Returns:
            Tuple (liste des embeds, liste des parents de tournoi).
        """
        # Création des embeds
        past_embed = self._create_base_embed(
            f"Derniers matchs de {name}", footer_text="Source: Liquipedia"
        )
        ongoing_embed = self._create_base_embed(
            f"Match en cours de {name}", footer_text="Source: Liquipedia"
        )
        upcoming_embed = self._create_base_embed(
            f"Prochains matchs de {name}", footer_text="Source: Liquipedia"
        )

        parents: List[str] = []
        current_time = datetime.now().timestamp()
        past_count, upcoming_count = 0, 0

        for match in data.get("result", []):
            # Collecter les parents de tournoi
            parent = match.get("parent")
            if parent and parent not in parents:
                parents.append(parent)
            
            # Calculer les scores
            score_1, score_2 = self._calculate_match_scores(match)
            match_timestamp = match["extradata"].get("timestamp", 0)
            
            # Catégoriser le match
            if match_timestamp < current_time:
                if match.get("finished") == 0:
                    # Match en cours
                    self._add_ongoing_match_fields(ongoing_embed, match, score_1, score_2)
                elif match.get("finished") == 1 and past_count < MAX_PAST_MATCHES:
                    # Match terminé
                    past_count = self._add_past_match_field(
                        past_embed, match, score_1, score_2, name, past_count
                    )
            elif upcoming_count < MAX_UPCOMING_MATCHES:
                # Match à venir
                upcoming_count = self._add_upcoming_match_field(
                    upcoming_embed, match, upcoming_count
                )

        # Filtrer les embeds vides
        embeds_to_return = [
            embed
            for embed in (past_embed, ongoing_embed, upcoming_embed)
            if embed.fields
        ]
        
        logger.debug(f"Embeds créés: {[embed.title for embed in embeds_to_return]}")
        logger.debug(f"Parents de tournoi: {parents}")
        
        return embeds_to_return, parents

    @staticmethod
    def _calculate_match_scores(match: Dict[str, Any]) -> Tuple[int, int]:
        """Calcule les scores d'un match."""
        games = match.get("match2games", [])
        score_1 = sum(1 for game in games if game.get("winner") == "1")
        score_2 = sum(1 for game in games if game.get("winner") == "2")
        return score_1, score_2

    def _add_ongoing_match_fields(
        self, embed: Embed, match: Dict, score_1: int, score_2: int
    ) -> None:
        """Ajoute les champs d'un match en cours à l'embed."""
        fields = self.format_ongoing_match(match, score_1, score_2)
        for field in fields:
            embed.add_field(name=field["name"], value=field["value"], inline=False)

    def _add_past_match_field(
        self, embed: Embed, match: Dict, score_1: int, score_2: int, name: str, count: int
    ) -> int:
        """Ajoute un match passé et retourne le nouveau compteur."""
        field = self.format_past_match(match, score_1, score_2, name)
        embed.add_field(name=field["name"], value=field["value"], inline=True)
        count += 1
        self._add_alignment_field_if_needed(embed, count)
        return count

    def _add_upcoming_match_field(
        self, embed: Embed, match: Dict, count: int
    ) -> int:
        """Ajoute un match à venir et retourne le nouveau compteur."""
        field = self.format_upcoming_match(match)
        embed.add_field(name=field["name"], value=field["value"], inline=True)
        count += 1
        self._add_alignment_field_if_needed(embed, count)
        return count

    @staticmethod
    def _add_alignment_field_if_needed(embed: Embed, count: int) -> None:
        """Ajoute un champ vide pour l'alignement si nécessaire."""
        if count % 2 != 0:
            embed.add_field(name="\u200b", value="\u200b", inline=True)

    @Task.create(IntervalTrigger(minutes=SCHEDULE_INTERVAL_MINUTES))
    async def mdi_schedule(self) -> None:
        """Tâche planifiée pour mettre à jour les infos MDI/Great Push."""
        try:
            data, dungeons = await get_table_data()
            infos = await self.mdi_infos()
            
            dungeons = ensure_six_elements(dungeons, "???")
            embeds = self._create_mdi_embeds(infos, data, dungeons)
            
            if self.wow_message:
                await self.wow_message.edit(
                    content="<:MDRBelieve:973667607439892530>", embeds=embeds
                )
        except Exception as e:
            logger.exception(f"Erreur dans mdi_schedule: {e}")

    def _create_mdi_embeds(
        self, infos: Dict[str, Any], teams_data: List[str], dungeons: List[str]
    ) -> List[Embed]:
        """Crée les embeds pour le MDI."""
        start = timestamp_converter(infos["start_date"]).format(TimestampStyles.LongDate)
        end = timestamp_converter(infos["end_date"]).format(TimestampStyles.LongDate)
        
        infos_str = (
            f"Du {start} au {end}\n"
            f"Cashprize: **${infos['prizepool']} USD**\n\n"
            f"**Day 1:** 6 équipes, 3 donjons ({', '.join(dungeons[:3])})\n"
            f"**Day 2:** 6 équipes, 5 donjons ({', '.join(dungeons[:5])})\n"
            f"**Day 3:** 6 équipes, 6 donjons ({', '.join(dungeons)})"
        )
        
        embed_infos = Embed(
            title=infos["name"],
            description=infos_str,
            color=DEFAULT_EMBED_COLOR,
            thumbnail=infos["icon"],
        )
        embed_infos.set_footer(text="Source: Liquipedia")
        
        # Embed des données de classement
        embed_data = self._create_base_embed(infos["name"], footer_text="Source: Raider.io")
        
        # Ajouter les classements
        self._add_chunked_fields(embed_data, "Classement", teams_data)
        
        return [embed_infos, embed_data]

    def _add_chunked_fields(
        self, embed: Embed, title: str, data_list: List[str], chunk_size: int = 1024
    ) -> None:
        """Ajoute des champs avec découpage automatique pour respecter la limite Discord."""
        if not data_list:
            return
        
        chunks = self._chunk_data(data_list, chunk_size)
        for index, chunk in enumerate(chunks):
            field_name = title if index == 0 else "\u200b"
            embed.add_field(name=field_name, value=chunk)

    @staticmethod
    def _chunk_data(data_list: List[str], chunk_size: int = 1024) -> List[str]:
        """Découpe une liste de données en chunks pour respecter les limites Discord."""
        chunks = []
        current_chunk = "```ansi\n"
        
        for item in data_list:
            if len(current_chunk) + len(item) + 5 > chunk_size:
                chunks.append(current_chunk + "```")
                current_chunk = "```ansi\n"
            current_chunk += item + "\n"
        
        if current_chunk != "```ansi\n":
            chunks.append(current_chunk + "```")
        
        return chunks

    async def mdi_infos(self) -> Dict[str, Any]:
        """Récupère les informations du tournoi MDI actuel.
        
        Returns:
            Dictionnaire avec les infos du tournoi.
        """
        tournament = "The_Great_Push/Dragonflight/Season_4/Global_Finals"
        tournament_data = await self.liquipedia_request(
            "worldofwarcraft",
            "tournament",
            f"[[pagename::{tournament}]]",
            query="startdate, enddate, name, prizepool, iconurl",
        )
        
        if not tournament_data.get("result"):
            logger.warning(f"Aucune donnée trouvée pour le tournoi: {tournament}")
            return {}
        
        result = tournament_data["result"][0]
        return {
            "name": result.get("name", "Tournoi inconnu"),
            "start_date": result.get("startdate", ""),
            "end_date": result.get("enddate", ""),
            "prizepool": result.get("prizepool", "0"),
            "icon": result.get("iconurl", ""),
        }
