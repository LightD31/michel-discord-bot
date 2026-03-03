"""Extension Esport Tracker pour le suivi des matchs esports.

Cette extension permet de suivre les matchs de plusieurs équipes esport
via l'API Liquipedia et Raider.io, avec l'API VLR.gg comme failover.
La source avec les données les plus récentes est automatiquement sélectionnée.

Configuration par serveur via le dashboard web (moduleLiquipedia):
- notificationChannelId: salon pour les notifications live
- teams: liste d'équipes, chacune avec:
    - name: nom de l'équipe
    - game: jeu (valorant, leagueoflegends, counterstrike, etc.)
    - vlrTeamId: ID VLR.gg (optionnel, pour le fallback Valorant)
    - liquipediaName: nom sur Liquipedia (optionnel, défaut = name)
    - channelMessageId: "channelId:messageId" pour le planning (optionnel)
"""

import asyncio
from typing import List, Dict, Any, Tuple, Optional
from dataclasses import dataclass, field
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
config, module_configs, enabled_servers = load_config("moduleLiquipedia")

# Constants
API_KEY = config.get("liquipedia", {}).get("liquipediaApiKey", "")
LIQUIPEDIA_API_URL = "https://api.liquipedia.net/api/v3"
DEFAULT_EMBED_COLOR = 0xE04747
LIVE_EMBED_COLOR = 0x00FF00  # Vert pour les matchs en direct
MAX_PAST_MATCHES = 6
MAX_UPCOMING_MATCHES = 6
SCHEDULE_INTERVAL_MINUTES = 5
LIVE_UPDATE_INTERVAL_MINUTES = 1
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


@dataclass
class TeamConfig:
    """Configuration d'une équipe à suivre."""
    name: str
    game: str = "valorant"
    vlr_team_id: Optional[str] = None
    liquipedia_name: Optional[str] = None
    channel_id: Optional[str] = None
    message_id: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TeamConfig":
        """Construit depuis un dict de config."""
        channel_id = None
        message_id = None
        cm = data.get("channelMessageId", "")
        if cm and ":" in cm:
            parts = cm.split(":", 1)
            channel_id = parts[0]
            message_id = parts[1]
        return cls(
            name=data.get("name", "Unknown"),
            game=data.get("game", "valorant"),
            vlr_team_id=data.get("vlrTeamId") or None,
            liquipedia_name=data.get("liquipediaName") or None,
            channel_id=channel_id,
            message_id=message_id,
        )

    @property
    def lp_name(self) -> str:
        """Nom à utiliser pour les requêtes Liquipedia."""
        return self.liquipedia_name or self.name


@dataclass
class TeamState:
    """État de suivi d'une équipe."""
    team_config: TeamConfig
    schedule_message: Any = None
    notification_channel: Any = None
    ongoing_matches: Dict[str, Any] = field(default_factory=dict)
    live_messages: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ServerState:
    """État de suivi d'un serveur."""
    server_id: str
    notification_channel_id: Optional[str] = None
    notification_channel: Any = None
    teams: Dict[str, TeamState] = field(default_factory=dict)


class Liquipedia(Extension):
    """Extension pour le suivi des matchs esports via Liquipedia.

    Supporte plusieurs serveurs, chacun avec plusieurs équipes à suivre.
    """

    def __init__(self, bot: Client) -> None:
        self.bot = bot
        self._headers = {"Authorization": f"Apikey {API_KEY}"} if API_KEY else {}
        self._servers: Dict[str, ServerState] = {}

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
            srv_config = module_configs.get(server_id, {})
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
                    notification_channel=server_state.notification_channel,
                )

                # Charger le message de planning si configuré
                if team_cfg.channel_id and team_cfg.message_id:
                    try:
                        channel = await self.bot.fetch_channel(team_cfg.channel_id)
                        if channel and hasattr(channel, "fetch_message"):
                            team_state.schedule_message = await channel.fetch_message(
                                team_cfg.message_id
                            )
                            logger.info(
                                f"Serveur {server_id}: message de planning chargé pour {team_cfg.name}"
                            )
                    except Exception as e:
                        logger.warning(
                            f"Serveur {server_id}: impossible de charger le message de planning "
                            f"pour {team_cfg.name}: {e}"
                        )

                server_state.teams[team_cfg.name] = team_state

            self._servers[server_id] = server_state
            logger.info(
                f"Serveur {server_id}: {len(server_state.teams)} équipe(s) initialisée(s) "
                f"({', '.join(server_state.teams.keys())})"
            )

    # ── Tâches planifiées ────────────────────────────────────────────

    @Task.create(IntervalTrigger(minutes=SCHEDULE_INTERVAL_MINUTES))
    async def schedule(self) -> None:
        """Tâche planifiée pour mettre à jour les plannings de toutes les équipes."""
        logger.debug("Exécution de la tâche schedule")
        for server_id, server_state in self._servers.items():
            for team_name, team_state in server_state.teams.items():
                try:
                    await self._update_team_schedule(team_state)
                except Exception as e:
                    logger.exception(
                        f"Erreur schedule pour {team_name} (serveur {server_id}): {e}"
                    )

    @Task.create(IntervalTrigger(minutes=LIVE_UPDATE_INTERVAL_MINUTES))
    async def live_update(self) -> None:
        """Tâche planifiée pour mettre à jour les scores des matchs en cours."""
        for server_id, server_state in self._servers.items():
            for team_name, team_state in server_state.teams.items():
                if not team_state.ongoing_matches:
                    continue
                try:
                    await self._update_team_live(team_state)
                except Exception as e:
                    logger.exception(
                        f"Erreur live_update pour {team_name} (serveur {server_id}): {e}"
                    )

    # ── Logique de mise à jour par équipe ────────────────────────────

    async def _update_team_schedule(self, team_state: TeamState) -> None:
        """Met à jour le planning d'une équipe."""
        tc = team_state.team_config
        embeds, ongoing_matches = await self._fetch_team_schedule_with_tracking(tc)
        await self._handle_match_transitions(ongoing_matches, team_state)
        if team_state.schedule_message and embeds:
            await team_state.schedule_message.edit(embeds=embeds)

    async def _update_team_live(self, team_state: TeamState) -> None:
        """Met à jour les scores live d'une équipe."""
        tc = team_state.team_config
        logger.debug(
            f"Mise à jour live de {len(team_state.ongoing_matches)} match(s) pour {tc.name}"
        )
        lp_success = await self._live_update_liquipedia(team_state)
        if not lp_success and tc.game == "valorant":
            logger.info(f"Basculement sur VLR.gg pour la mise à jour live de {tc.name}")
            await self._live_update_vlrgg(team_state)

    async def _live_update_liquipedia(self, team_state: TeamState) -> bool:
        """Met à jour les scores live via Liquipedia."""
        tc = team_state.team_config
        try:
            date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            data = await self.liquipedia_request(
                tc.game,
                "match",
                f"[[opponent::{tc.lp_name}]] AND [[date::>{date}]]",
                limit=5,
                order="date DESC",
            )

            matches_to_remove = []
            for match in data.get("result", []):
                match_id = match.get("match2id") or match.get("pagename", "")
                if match_id in team_state.ongoing_matches:
                    if match.get("finished") == 1:
                        await self._handle_match_ended(match, match_id, team_state)
                        matches_to_remove.append(match_id)
                    else:
                        await self._update_live_message(match, match_id, team_state)

            for match_id in matches_to_remove:
                team_state.ongoing_matches.pop(match_id, None)

            return True
        except Exception as e:
            logger.warning(f"Échec de la mise à jour live Liquipedia pour {tc.name}: {e}")
            return False

    async def _live_update_vlrgg(self, team_state: TeamState) -> None:
        """Met à jour les scores live via VLR.gg (fallback Valorant)."""
        tc = team_state.team_config
        try:
            vlr_data = await self._fetch_vlrgg_data(tc)
            if not vlr_data:
                return

            vlr_live = vlr_data.get("live", [])

            for match_id in list(team_state.ongoing_matches.keys()):
                if match_id.startswith("vlrgg_"):
                    still_live = any(
                        f"vlrgg_{m.get('team1', '')}_{m.get('team2', '')}" == match_id
                        for m in vlr_live
                    )
                    if not still_live:
                        message = team_state.live_messages.pop(match_id, None)
                        if message:
                            try:
                                await message.edit(content="**Match terminé!**")
                            except Exception:
                                pass
                        team_state.ongoing_matches.pop(match_id, None)
                    else:
                        for m in vlr_live:
                            vlr_id = f"vlrgg_{m.get('team1', '')}_{m.get('team2', '')}"
                            if vlr_id == match_id:
                                await self._update_vlrgg_live_message(
                                    m, match_id, team_state
                                )
                                break
        except Exception as e:
            logger.warning(f"Échec de la mise à jour live VLR.gg pour {tc.name}: {e}")

    # ── Notifications live ───────────────────────────────────────────

    async def _handle_match_transitions(
        self, ongoing_matches: Dict[str, Any], team_state: TeamState
    ) -> None:
        """Gère les transitions de matchs (début/fin)."""
        tc = team_state.team_config

        # Nouveaux matchs
        for match_id, match in ongoing_matches.items():
            if match_id not in team_state.ongoing_matches:
                logger.info(f"Nouveau match détecté pour {tc.name}: {match_id}")
                if match_id.startswith("vlrgg_"):
                    await self._send_vlrgg_match_started_notification(match, team_state)
                else:
                    await self._send_match_started_notification(match, team_state)
                team_state.ongoing_matches[match_id] = match

        # Matchs terminés
        finished = [mid for mid in team_state.ongoing_matches if mid not in ongoing_matches]
        for match_id in finished:
            logger.info(f"Match terminé détecté pour {tc.name}: {match_id}")
            team_state.ongoing_matches.pop(match_id, None)

    async def _send_match_started_notification(
        self, match: Dict[str, Any], team_state: TeamState
    ) -> None:
        """Envoie une notification Liquipedia quand un match commence."""
        channel = team_state.notification_channel
        if not channel:
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
            embed.add_field(
                name="Score",
                value=f"**{shortname_1}** {score_1} - {score_2} **{shortname_2}**",
                inline=False,
            )
            embed.set_footer(text="Mise à jour automatique • Source: Liquipedia")

            match_id = match.get("match2id") or match.get("pagename", "")
            message = await channel.send(
                content=f"🔴 **Match en direct — {team_state.team_config.name}!**",
                embeds=[embed],
            )
            team_state.live_messages[match_id] = message
            logger.info(f"Notification envoyée pour {name_1} vs {name_2}")
        except Exception as e:
            logger.exception(f"Erreur notification Liquipedia: {e}")

    async def _send_vlrgg_match_started_notification(
        self, match: Dict[str, Any], team_state: TeamState
    ) -> None:
        """Envoie une notification VLR.gg quand un match commence."""
        channel = team_state.notification_channel
        if not channel:
            return

        try:
            team1 = match.get("team1", "???")
            team2 = match.get("team2", "???")
            score1 = match.get("score1", "0")
            score2 = match.get("score2", "0")
            event = match.get("match_event", "Tournoi")

            embed = Embed(
                title=f"🔴 LIVE: {team1} vs {team2}",
                description=f"**{event}**\n\nLe match vient de commencer!",
                color=LIVE_EMBED_COLOR,
                timestamp=Timestamp.now(),
            )
            embed.add_field(
                name="Score",
                value=f"**{team1}** {score1} - {score2} **{team2}**",
                inline=False,
            )
            embed.set_footer(text="Mise à jour automatique • Source: VLR.gg")

            match_id = f"vlrgg_{team1}_{team2}"
            message = await channel.send(
                content=f"🔴 **Match en direct — {team_state.team_config.name}!**",
                embeds=[embed],
            )
            team_state.live_messages[match_id] = message
            logger.info(f"Notification VLR.gg envoyée pour {team1} vs {team2}")
        except Exception as e:
            logger.exception(f"Erreur notification VLR.gg: {e}")

    async def _update_live_message(
        self, match: Dict[str, Any], match_id: str, team_state: TeamState
    ) -> None:
        """Met à jour le message de score en direct (Liquipedia)."""
        message = team_state.live_messages.get(match_id)
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
            embed.add_field(
                name="Score",
                value=f"**{shortname_1}** {score_1} - {score_2} **{shortname_2}**",
                inline=False,
            )
            map_veto = match["extradata"].get("mapveto", {})
            games_details = self._format_live_games(
                match["match2games"], map_veto, shortname_1, shortname_2
            )
            if games_details:
                embed.add_field(name="Maps", value=games_details, inline=False)
            embed.set_footer(text="Mise à jour automatique • Source: Liquipedia")

            await message.edit(embeds=[embed])
            logger.debug(f"Message live mis à jour pour {match_id}: {score_1}-{score_2}")
        except Exception as e:
            logger.exception(f"Erreur mise à jour message live: {e}")

    async def _update_vlrgg_live_message(
        self, match: Dict[str, Any], match_id: str, team_state: TeamState
    ) -> None:
        """Met à jour le message de score en direct (VLR.gg)."""
        message = team_state.live_messages.get(match_id)
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
                inline=False,
            )
            if current_map:
                embed.add_field(name="Map actuelle", value=current_map, inline=False)

            t1_ct = match.get("team1_round_ct", "")
            t1_t = match.get("team1_round_t", "")
            t2_ct = match.get("team2_round_ct", "")
            t2_t = match.get("team2_round_t", "")
            if t1_ct and t1_ct != "N/A":
                embed.add_field(
                    name="Rounds",
                    value=f"CT: {t1_ct}-{t2_ct} | T: {t1_t}-{t2_t}",
                    inline=False,
                )
            embed.set_footer(text="Mise à jour automatique • Source: VLR.gg")

            await message.edit(embeds=[embed])
            logger.debug(f"Message live VLR.gg mis à jour pour {match_id}: {score1}-{score2}")
        except Exception as e:
            logger.exception(f"Erreur mise à jour VLR.gg message live: {e}")

    async def _handle_match_ended(
        self, match: Dict[str, Any], match_id: str, team_state: TeamState
    ) -> None:
        """Gère la fin d'un match."""
        message = team_state.live_messages.pop(match_id, None)
        if not message:
            return

        tc = team_state.team_config
        try:
            name_1, name_2, shortname_1, shortname_2 = self._get_match_teams(match)
            score_1, score_2 = self._calculate_match_scores(match)

            winner = int(match.get("winner", 0)) - 1
            winner_name = match["match2opponents"][winner]["name"] if winner >= 0 else "???"

            is_victory = winner_name == tc.lp_name
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
            embed.add_field(
                name="Score Final",
                value=f"**{shortname_1}** {score_1} - {score_2} **{shortname_2}**",
                inline=False,
            )
            map_veto = match["extradata"].get("mapveto", {})
            games_details = self._format_games_list(
                match["match2games"], map_veto, shortname_1, shortname_2
            )
            if games_details:
                embed.add_field(name="Détail des maps", value=games_details, inline=False)
            embed.set_footer(text="Match terminé • Source: Liquipedia")

            await message.edit(
                content=f"{result_emoji} **Match terminé!** {result_emoji}",
                embeds=[embed],
            )
            logger.info(f"Match terminé: {name_1} {score_1}-{score_2} {name_2}")
        except Exception as e:
            logger.exception(f"Erreur gestion fin de match: {e}")

    # ── Récupération des données ─────────────────────────────────────

    async def _fetch_team_schedule_with_tracking(
        self, tc: TeamConfig
    ) -> Tuple[List[Embed], Dict[str, Any]]:
        """Récupère le planning avec suivi des matchs en cours.

        Utilise Liquipedia comme source principale et VLR.gg comme failover
        (uniquement pour Valorant).
        """
        lp_task = self._fetch_liquipedia_data(tc)
        vlr_task = (
            self._fetch_vlrgg_data(tc) if tc.game == "valorant" else asyncio.sleep(0)
        )

        lp_result, vlr_result = await asyncio.gather(
            lp_task, vlr_task, return_exceptions=True
        )

        lp_ok = not isinstance(lp_result, Exception) and lp_result is not None
        vlr_ok = (
            not isinstance(vlr_result, Exception)
            and vlr_result is not None
            and isinstance(vlr_result, dict)
        )

        source = DataSource.LIQUIPEDIA
        if lp_ok and vlr_ok:
            assert isinstance(lp_result, dict) and isinstance(vlr_result, dict)
            if self._vlrgg_has_fresher_data(lp_result, vlr_result):
                source = DataSource.VLRGG
                logger.info(f"{tc.name}: VLR.gg sélectionné (données plus récentes)")
            else:
                logger.info(f"{tc.name}: Liquipedia sélectionné")
        elif not lp_ok and vlr_ok:
            source = DataSource.VLRGG
            logger.warning(f"{tc.name}: Liquipedia indisponible, basculement VLR.gg")
        elif not lp_ok and not vlr_ok:
            logger.error(f"{tc.name}: les deux sources ont échoué")
            raise Exception(
                f"Les deux sources de données sont indisponibles pour {tc.name}"
            )

        if source == DataSource.VLRGG:
            assert isinstance(vlr_result, dict)
            embeds = self._build_vlrgg_embeds(vlr_result, tc.name)
            ongoing = self._extract_vlrgg_ongoing(vlr_result)
            return embeds, ongoing
        else:
            assert isinstance(lp_result, dict)
            embeds, pagenames = await self.make_schedule_embed(lp_result, tc.name)

            ongoing_matches: Dict[str, Any] = {}
            current_time = datetime.now().timestamp()
            for match in lp_result.get("result", []):
                match_timestamp = match.get("extradata", {}).get("timestamp", 0)
                if match_timestamp < current_time and match.get("finished") == 0:
                    match_id = match.get("match2id") or match.get("pagename", "")
                    ongoing_matches[match_id] = match

            for pagename in pagenames:
                standings_embeds = await self._fetch_tournament_standings(pagename, tc.game)
                embeds.extend(standings_embeds)

            return embeds, ongoing_matches

    async def _fetch_liquipedia_data(self, tc: TeamConfig) -> Dict[str, Any]:
        """Récupère les données Liquipedia pour une équipe."""
        date = (datetime.now() - timedelta(weeks=MATCH_HISTORY_WEEKS)).strftime("%Y-%m-%d")
        return await self.liquipedia_request(
            tc.game,
            "match",
            f"[[opponent::{tc.lp_name}]] AND [[date::>{date}]]",
            limit=15,
            order="date ASC",
        )

    async def _fetch_vlrgg_data(self, tc: TeamConfig) -> Optional[Dict[str, Any]]:
        """Récupère les données VLR.gg pour une équipe (Valorant uniquement)."""
        try:
            data = await vlrgg_fetch_all(tc.name, team_id=tc.vlr_team_id)
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

    def _vlrgg_has_fresher_data(
        self, lp_data: Dict[str, Any], vlr_data: Dict[str, Any]
    ) -> bool:
        """Détermine si VLR.gg a des données plus fraîches que Liquipedia."""
        vlr_live = vlr_data.get("live", [])
        current_time = datetime.now().timestamp()

        lp_ongoing_count = sum(
            1
            for m in lp_data.get("result", [])
            if m.get("finished") == 0
            and m.get("extradata", {}).get("timestamp", 0) < current_time
        )

        if vlr_live and lp_ongoing_count == 0:
            logger.info("VLR.gg détecte un match live que Liquipedia ne montre pas")
            return True

        vlr_results = vlr_data.get("results", [])
        most_recent_vlr = get_most_recent_result_time(vlr_results)

        if most_recent_vlr:
            lp_finished = [
                m for m in lp_data.get("result", []) if m.get("finished") == 1
            ]
            if lp_finished:
                latest_lp_ts = max(
                    m.get("extradata", {}).get("timestamp", 0) for m in lp_finished
                )
                latest_lp = (
                    datetime.fromtimestamp(latest_lp_ts) if latest_lp_ts else None
                )
                if latest_lp and most_recent_vlr > latest_lp:
                    return True
            elif vlr_results:
                return True

        vlr_upcoming = vlr_data.get("upcoming", [])
        lp_upcoming = [
            m
            for m in lp_data.get("result", [])
            if m.get("extradata", {}).get("timestamp", 0) > current_time
        ]
        if vlr_upcoming and not lp_upcoming:
            return True

        return False

    # ── Construction des embeds ──────────────────────────────────────

    def _build_vlrgg_embeds(
        self, vlr_data: Dict[str, Any], team: str
    ) -> List[Embed]:
        """Construit les embeds Discord à partir des données VLR.gg."""
        embeds = []

        results = vlr_data.get("results", [])
        if results:
            past_embed = self._create_base_embed(
                f"Derniers matchs de {team}", footer_text="Source: VLR.gg (failover)"
            )
            for i, match in enumerate(results[:MAX_PAST_MATCHES]):
                field = self._format_vlrgg_result(match, team)
                past_embed.add_field(
                    name=field["name"], value=field["value"], inline=True
                )
                if (i + 1) % 2 != 0:
                    past_embed.add_field(name="\u200b", value="\u200b", inline=True)
            embeds.append(past_embed)

        live = vlr_data.get("live", [])
        if live:
            live_embed = self._create_base_embed(
                f"Match en cours de {team}", footer_text="Source: VLR.gg (failover)"
            )
            for match in live:
                field = self._format_vlrgg_live(match)
                live_embed.add_field(
                    name=field["name"], value=field["value"], inline=False
                )
            embeds.append(live_embed)

        upcoming = vlr_data.get("upcoming", [])
        if upcoming:
            upcoming_embed = self._create_base_embed(
                f"Prochains matchs de {team}",
                footer_text="Source: VLR.gg (failover)",
            )
            for i, match in enumerate(upcoming[:MAX_UPCOMING_MATCHES]):
                field = self._format_vlrgg_upcoming(match)
                upcoming_embed.add_field(
                    name=field["name"], value=field["value"], inline=True
                )
                if (i + 1) % 2 != 0:
                    upcoming_embed.add_field(
                        name="\u200b", value="\u200b", inline=True
                    )
            embeds.append(upcoming_embed)

        return embeds

    def _format_vlrgg_result(
        self, match: Dict[str, Any], team: str
    ) -> Dict[str, str]:
        """Formate un résultat VLR.gg pour l'affichage."""
        team1 = match.get("team1", "???")
        team2 = match.get("team2", "???")
        score1 = match.get("score1", "?")
        score2 = match.get("score2", "?")
        time_completed = match.get("time_completed", "?")
        tournament = match.get("tournament_name", match.get("round_info", ""))

        try:
            s1, s2 = int(score1), int(score2)
            team_lower = team.lower()
            is_team1 = team_lower in team1.lower()
            team_won = (is_team1 and s1 > s2) or (not is_team1 and s2 > s1)
            resultat = "Gagné ✅" if team_won else "Perdu ❌"
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

        t1_ct = match.get("team1_round_ct", "")
        t1_t = match.get("team1_round_t", "")
        t2_ct = match.get("team2_round_ct", "")
        t2_t = match.get("team2_round_t", "")
        if t1_ct and t1_ct != "N/A":
            value += f"Rounds — CT: {t1_ct}-{t2_ct} | T: {t1_t}-{t2_t}"

        return {
            "name": f"🔴 {team1} {score1}-{score2} {team2}",
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
        """Extrait les matchs en cours depuis les données VLR.gg."""
        ongoing: Dict[str, Any] = {}
        for match in vlr_data.get("live", []):
            match_id = f"vlrgg_{match.get('team1', '')}_{match.get('team2', '')}"
            ongoing[match_id] = match
        return ongoing

    def _format_live_games(
        self,
        games: List[Dict],
        map_veto: Dict,
        shortname_1: str,
        shortname_2: str,
    ) -> Optional[str]:
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

    # ── Tournois & classements ───────────────────────────────────────

    async def _fetch_tournament_standings(
        self, pagename: str, game: str = "valorant"
    ) -> List[Embed]:
        """Récupère les classements d'un tournoi."""
        embeds = []
        tournament = await self.liquipedia_request(
            game,
            "tournament",
            f"[[pagename::{pagename}]]",
            query="participantsnumber, name",
        )

        if not tournament.get("result"):
            return embeds

        participants_number = int(tournament["result"][0]["participantsnumber"])
        tournament_name = tournament["result"][0]["name"]

        standings = await self.liquipedia_request(
            game,
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

    # ── API Liquipedia ───────────────────────────────────────────────

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
        """Effectue une requête vers l'API Liquipedia."""
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
            if value
        }
        url = f"{LIQUIPEDIA_API_URL}/{datapoint}"
        logger.debug(f"Requête Liquipedia: {url} | params: {params}")
        return await fetch(url, headers=self._headers, params=params, return_type="json")

    # ── Classements ──────────────────────────────────────────────────

    async def organize_standings(
        self, data: Dict[str, Any]
    ) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
        """Organise les classements par page et par semaine."""
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

            organized.setdefault(pageid, {}).setdefault(roundindex, []).append(
                team_data
            )

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
        """Crée un embed de classement."""
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
        standing_str = self._format_status(
            team["currentstatus"], str(team["standing"]), bold=True
        )
        team_str = self._format_status(
            team["definitestatus"], f"{team['team']:<23}"
        )
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

    # ── Utilitaires d'embeds ─────────────────────────────────────────

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

    def _get_match_teams(
        self, match: Dict[str, Any]
    ) -> Tuple[str, str, str, str]:
        """Extrait les noms des équipes d'un match Liquipedia."""
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
        self, match: Dict[str, Any], score_1: int, score_2: int, name: str
    ) -> Dict[str, str]:
        """Formate un match passé pour l'affichage."""
        name_1, name_2, shortname_1, shortname_2 = self._get_match_teams(match)
        winner = int(match["winner"]) - 1
        winner_name = match["match2opponents"][winner]["name"]
        date = timestamp_converter(match["extradata"]["timestamp"])

        resultat = "Gagné ✅" if winner_name == name else "Perdu ❌"

        map_veto = match["extradata"].get("mapveto", {})
        games = self._format_games_list(
            match["match2games"], map_veto, shortname_1, shortname_2
        )

        return {
            "name": f"{name_1} {score_1}-{score_2} {name_2} (Bo{match['bestof']})",
            "value": f"{match['tickername']}\n{date}\n{games}{resultat}",
        }

    def _format_games_list(
        self,
        games: List[Dict],
        map_veto: Dict,
        shortname_1: str,
        shortname_2: str,
    ) -> str:
        """Formate la liste des games d'un match."""
        result = ""
        for game in games:
            if game.get("resulttype") == "np":
                break
            map_name = game["map"]
            veto_info = self._get_veto_info(
                map_name, map_veto, shortname_1, shortname_2
            )
            scores = game.get("scores", [0, 0])
            game_result = self._format_game_score(int(scores[0]), int(scores[1]))
            result += f"**{map_name}** : {game_result} {veto_info}\n"
        return result

    def format_ongoing_match(
        self, match: Dict[str, Any], score_1: int, score_2: int
    ) -> List[Dict[str, str]]:
        """Formate un match en cours pour l'affichage."""
        name_1, name_2, shortname_1, shortname_2 = self._get_match_teams(match)

        embeds = [
            {
                "name": f"🔴 {name_1} {score_1}-{score_2} {name_2} en Bo{match['bestof']}",
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
        veto_info = self._get_veto_info(
            map_name, map_veto, shortname_1, shortname_2
        )

        if game.get("resulttype") != "np" and game.get("scores"):
            scores = game["scores"]
            game_result = self._format_game_score(int(scores[0]), int(scores[1]))
            value = f"{shortname_1} {game_result} {shortname_2}"
            if players_info:
                value += f"\n{players_info}"
        else:
            value = "\u200b"

        return {"name": f"**{map_name}** {veto_info}", "value": value}

    def _format_players_info(
        self, game: Dict, name_1: str, name_2: str
    ) -> str:
        """Formate les infos des joueurs en deux colonnes."""
        participants = game.get("participants", {})
        if not isinstance(participants, dict):
            return ""

        players_team1 = []
        players_team2 = []

        for participant in (
            participants.values() if isinstance(participants, dict) else participants
        ):
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
        """Crée les embeds de planning pour une équipe."""
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
            parent = match.get("parent")
            if parent and parent not in parents:
                parents.append(parent)

            score_1, score_2 = self._calculate_match_scores(match)
            match_timestamp = match["extradata"].get("timestamp", 0)

            if match_timestamp < current_time:
                if match.get("finished") == 0:
                    self._add_ongoing_match_fields(
                        ongoing_embed, match, score_1, score_2
                    )
                elif match.get("finished") == 1 and past_count < MAX_PAST_MATCHES:
                    past_count = self._add_past_match_field(
                        past_embed, match, score_1, score_2, name, past_count
                    )
            elif upcoming_count < MAX_UPCOMING_MATCHES:
                upcoming_count = self._add_upcoming_match_field(
                    upcoming_embed, match, upcoming_count
                )

        embeds_to_return = [
            embed
            for embed in (past_embed, ongoing_embed, upcoming_embed)
            if embed.fields
        ]

        logger.debug(f"Embeds créés: {[embed.title for embed in embeds_to_return]}")
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
        for f in fields:
            embed.add_field(
                name=f["name"], value=f["value"], inline=False
            )

    def _add_past_match_field(
        self,
        embed: Embed,
        match: Dict,
        score_1: int,
        score_2: int,
        name: str,
        count: int,
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

    # ── MDI / WoW (legacy, non lié aux teams) ────────────────────────

    @Task.create(IntervalTrigger(minutes=SCHEDULE_INTERVAL_MINUTES))
    async def mdi_schedule(self) -> None:
        """Tâche planifiée pour mettre à jour les infos MDI/Great Push."""
        try:
            data, dungeons = await get_table_data()
            infos = await self.mdi_infos()

            dungeons = ensure_six_elements(dungeons, "???")
            _embeds = self._create_mdi_embeds(infos, data, dungeons)

            # TODO: configurer le message WoW via la config teams (_embeds non utilisé pour l'instant)
        except Exception as e:
            logger.exception(f"Erreur dans mdi_schedule: {e}")

    def _create_mdi_embeds(
        self,
        infos: Dict[str, Any],
        teams_data: List[str],
        dungeons: List[str],
    ) -> List[Embed]:
        """Crée les embeds pour le MDI."""
        start = timestamp_converter(infos["start_date"]).format(
            TimestampStyles.LongDate
        )
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

        embed_data = self._create_base_embed(
            infos["name"], footer_text="Source: Raider.io"
        )
        self._add_chunked_fields(embed_data, "Classement", teams_data)

        return [embed_infos, embed_data]

    def _add_chunked_fields(
        self,
        embed: Embed,
        title: str,
        data_list: List[str],
        chunk_size: int = 1024,
    ) -> None:
        """Ajoute des champs avec découpage automatique."""
        if not data_list:
            return
        chunks = self._chunk_data(data_list, chunk_size)
        for index, chunk in enumerate(chunks):
            field_name = title if index == 0 else "\u200b"
            embed.add_field(name=field_name, value=chunk)

    @staticmethod
    def _chunk_data(data_list: List[str], chunk_size: int = 1024) -> List[str]:
        """Découpe une liste en chunks pour les limites Discord."""
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
        """Récupère les informations du tournoi MDI actuel."""
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
