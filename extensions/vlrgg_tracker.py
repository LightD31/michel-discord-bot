"""Extension Esport Tracker pour le suivi des matchs Valorant via VLR.gg.

Cette extension permet de suivre les matchs de plusieurs équipes Valorant
via l'API VLR.gg (source unique).

Configuration par serveur via le dashboard web (moduleVlrgg):
- notificationChannelId: salon pour les notifications live
- teams: liste d'équipes, chacune avec:
    - name: nom de l'équipe
    - vlrTeamId: ID VLR.gg (requis)
    - channelMessageId: "channelId:messageId" pour le planning (optionnel)
"""

import json
import os
from dataclasses import dataclass, field
from typing import Any, Optional

from interactions import (
    Client,
    Embed,
    Extension,
    IntervalTrigger,
    Task,
    Timestamp,
    listen,
)

from src import logutil
from src.config_manager import CONFIG_PATH, load_config
from src.helpers import (
    SPACER_FIELD,
    Colors,
    format_discord_timestamp,
)
from src.mongodb import mongo_manager
from src.vlrgg import (
    expand_round_name,
    extract_match_id_from_url,
    fetch_match_details,
    format_vlr_date,
    parse_vlrgg_timestamp,
)
from src.vlrgg import (
    fetch_all_team_data as vlrgg_fetch_all,
)
from src.webui.schemas import SchemaBase, enabled_field, register_module, ui


@register_module("moduleVlrgg")
class VlrggConfig(SchemaBase):
    __label__ = "Esport Tracker (VLR.gg)"
    __description__ = "Suivi automatique des matchs d'équipes Valorant via VLR.gg."
    __icon__ = "🎮"
    __category__ = "Esport & Jeux"

    enabled: bool = enabled_field()
    notificationChannelId: str | None = ui(
        "Salon notifications",
        "channel",
        description="Salon pour les notifications de matchs en direct et résultats.",
    )
    teams: list[Any] = ui(
        "Équipes suivies",
        "teams",
        description=(
            "Liste des équipes Valorant à suivre. Chaque équipe nécessite un nom et un ID VLR.gg."
        ),
    )


logger = logutil.init_logger(os.path.basename(__file__))
config, module_config, enabled_servers = load_config("moduleVlrgg")

# Constants
DEFAULT_EMBED_COLOR = Colors.VLR
LIVE_EMBED_COLOR = Colors.SUCCESS
MAX_PAST_MATCHES = 6
MAX_UPCOMING_MATCHES = 6
SCHEDULE_INTERVAL_MINUTES = 2
LIVE_UPDATE_INTERVAL_MINUTES = 0.5


@dataclass
class TeamConfig:
    """Configuration d'une équipe à suivre."""

    name: str
    vlr_team_id: str | None = None
    channel_id: str | None = None
    message_id: str | None = None
    pin: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TeamConfig":
        """Construit depuis un dict de config."""
        channel_id = None
        message_id = None
        cm = data.get("channelMessageId", "")
        if cm:
            if ":" in cm:
                parts = cm.split(":", 1)
                channel_id = parts[0].strip() or None
                message_id = parts[1].strip() or None
            else:
                channel_id = cm.strip() or None
        return cls(
            name=data.get("name", "Unknown"),
            vlr_team_id=data.get("vlrTeamId") or None,
            channel_id=channel_id,
            message_id=message_id,
            pin=bool(data.get("pin", False)),
        )


def _save_team_channel_message(
    guild_id: str, team_name: str, channel_id: str, message_id: str
) -> None:
    """Update the channelMessageId for a specific team in config.json."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as file:
            data = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error("Could not read config for vlrgg team save: %s", e)
        return

    servers = data.setdefault("servers", {})
    guild = servers.setdefault(str(guild_id), {})
    mod = guild.setdefault("moduleVlrgg", {})
    teams = mod.setdefault("teams", [])
    combined = f"{channel_id}:{message_id}"
    for team in teams:
        if team.get("name") == team_name:
            team["channelMessageId"] = combined
            break
    else:
        teams.append({"name": team_name, "channelMessageId": combined})

    with open(CONFIG_PATH, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)
    logger.info("Saved channelMessageId for team %s on guild %s", team_name, guild_id)


@dataclass
class TeamState:
    """État de suivi d'une équipe."""

    team_config: TeamConfig
    server_id: str = ""
    schedule_message: Any = None
    notification_channel: Any = None
    ongoing_matches: dict[str, Any] = field(default_factory=dict)
    live_messages: dict[str, Any] = field(default_factory=dict)


@dataclass
class ServerState:
    """État de suivi d'un serveur."""

    server_id: str
    notification_channel_id: str | None = None
    notification_channel: Any = None
    teams: dict[str, TeamState] = field(default_factory=dict)


class VlrggTrackerExtension(Extension):
    """Extension pour le suivi des matchs Valorant via VLR.gg.

    Supporte plusieurs serveurs, chacun avec plusieurs équipes à suivre.
    """

    def __init__(self, bot: Client) -> None:
        self.bot = bot
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
                                        f"Serveur {server_id}: message {team_cfg.message_id} introuvable pour {team_cfg.name} ({e}); recréation"
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
                    logger.exception(f"Erreur schedule pour {team_name} (serveur {server_id}): {e}")

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
        embeds, ongoing_matches = await self._fetch_team_schedule(tc)
        await self._handle_match_transitions(ongoing_matches, team_state)
        if team_state.schedule_message and embeds:
            await team_state.schedule_message.edit(embeds=embeds)

    async def _update_team_live(self, team_state: TeamState) -> None:
        """Met à jour les scores live d'une équipe via VLR.gg."""
        tc = team_state.team_config
        logger.debug(
            f"Mise à jour live de {len(team_state.ongoing_matches)} match(s) pour {tc.name}"
        )
        await self._live_update_vlrgg(team_state)

    async def _live_update_vlrgg(self, team_state: TeamState) -> None:
        """Met à jour les scores live via VLR.gg."""
        tc = team_state.team_config
        try:
            vlr_data = await self._fetch_vlrgg_data(tc)
            if not vlr_data:
                return

            vlr_live = vlr_data.get("live", [])

            for match_id in list(team_state.ongoing_matches.keys()):
                still_live = any(self._make_vlr_match_id(m) == match_id for m in vlr_live)
                if not still_live:
                    # Match terminé — essayer de récupérer les détails pour un embed final
                    await self._handle_vlr_match_ended(match_id, team_state)
                else:
                    for m in vlr_live:
                        if self._make_vlr_match_id(m) == match_id:
                            await self._update_vlrgg_live_message(m, match_id, team_state)
                            break
        except Exception as e:
            logger.warning(f"Échec de la mise à jour live VLR.gg pour {tc.name}: {e}")

    # ── Notifications live ───────────────────────────────────────────

    async def _handle_match_transitions(
        self, ongoing_matches: dict[str, Any], team_state: TeamState
    ) -> None:
        """Gère les transitions de matchs (début/fin)."""
        tc = team_state.team_config

        # Nouveaux matchs
        for match_id, match in ongoing_matches.items():
            if match_id not in team_state.ongoing_matches:
                logger.info(f"Nouveau match détecté pour {tc.name}: {match_id}")
                await self._send_vlrgg_match_started_notification(match, team_state)
                team_state.ongoing_matches[match_id] = match

        # Matchs terminés
        finished = [mid for mid in team_state.ongoing_matches if mid not in ongoing_matches]
        for match_id in finished:
            logger.info(f"Match terminé détecté pour {tc.name}: {match_id}")
            team_state.ongoing_matches.pop(match_id, None)

    async def _send_vlrgg_match_started_notification(
        self, match: dict[str, Any], team_state: TeamState
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

            match_id = self._make_vlr_match_id(match)
            message = await channel.send(
                content=f"🔴 **Match en direct — {team_state.team_config.name}!**",
                embeds=[embed],
            )
            team_state.live_messages[match_id] = message
            await self._save_live_match(
                team_state.server_id,
                team_state.team_config.name,
                match_id,
                match,
                str(channel.id),
                str(message.id),
            )
            logger.info(f"Notification VLR.gg envoyée pour {team1} vs {team2}")
        except Exception as e:
            logger.exception(f"Erreur notification VLR.gg: {e}")

    async def _update_vlrgg_live_message(
        self, match: dict[str, Any], match_id: str, team_state: TeamState
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

    async def _handle_vlr_match_ended(self, match_id: str, team_state: TeamState) -> None:
        """Gère la fin d'un match VLR.gg — tente de récupérer les détails."""
        message = team_state.live_messages.pop(match_id, None)
        match_data = team_state.ongoing_matches.pop(match_id, None)
        await self._delete_live_match(team_state.server_id, match_id)
        if not message:
            return

        tc = team_state.team_config
        try:
            # Essayer de récupérer les détails du match pour un résumé riche
            details = None
            match_url = (match_data or {}).get("match_page", "")
            vlr_match_id = extract_match_id_from_url(match_url)
            if vlr_match_id:
                try:
                    details = await fetch_match_details(vlr_match_id)
                except Exception as e:
                    logger.warning(
                        f"Impossible de récupérer les détails du match {vlr_match_id}: {e}"
                    )

            # Extraire noms et scores depuis les détails si disponibles
            if details and details.get("teams"):
                teams_detail = details["teams"]
                team1 = teams_detail[0].get("name", "???") if len(teams_detail) > 0 else "???"
                team2 = teams_detail[1].get("name", "???") if len(teams_detail) > 1 else "???"
                score1 = str(teams_detail[0].get("score", "?")) if len(teams_detail) > 0 else "?"
                score2 = str(teams_detail[1].get("score", "?")) if len(teams_detail) > 1 else "?"
            else:
                team1 = (match_data or {}).get("team1", "???")
                team2 = (match_data or {}).get("team2", "???")
                score1 = (match_data or {}).get("score1", "?")
                score2 = (match_data or {}).get("score2", "?")

            # Déterminer victoire/défaite
            try:
                s1, s2 = int(score1), int(score2)
                # Préférer is_winner depuis les détails
                if details and details.get("teams"):
                    teams_detail = details["teams"]
                    tc_lower = tc.name.lower()
                    for td in teams_detail:
                        if tc_lower in td.get("name", "").lower():
                            team_won = td.get("is_winner", False)
                            break
                    else:
                        is_team1 = tc_lower in team1.lower()
                        team_won = (is_team1 and s1 > s2) or (not is_team1 and s2 > s1)
                else:
                    team_lower = tc.name.lower()
                    is_team1 = team_lower in team1.lower()
                    team_won = (is_team1 and s1 > s2) or (not is_team1 and s2 > s1)
            except (ValueError, TypeError):
                team_won = None

            if team_won is True:
                result_emoji = "🎉"
                result_text = "VICTOIRE"
                embed_color = Colors.SUCCESS
            elif team_won is False:
                result_emoji = "😢"
                result_text = "DÉFAITE"
                embed_color = Colors.ERROR
            else:
                result_emoji = "🏁"
                result_text = "TERMINÉ"
                embed_color = DEFAULT_EMBED_COLOR

            # Construire la description de l'événement
            event_name = ""
            round_info = ""
            if details and isinstance(details.get("event"), dict):
                from src.vlrgg import _clean_vlr_text, expand_round_name

                event_obj = details["event"]
                series = _clean_vlr_text(event_obj.get("series", ""))
                full_name = _clean_vlr_text(event_obj.get("name", ""))
                if full_name and series and series in full_name:
                    event_name = full_name[: full_name.index(series)].strip()
                else:
                    event_name = full_name
                round_info = expand_round_name(series) if series else ""
            if not event_name:
                event_name = (match_data or {}).get("match_event", "") or (match_data or {}).get(
                    "tournament_name", "Tournoi"
                )

            description = f"**{event_name}**"
            if round_info:
                description += f"\n{round_info}"

            embed = Embed(
                title=f"{result_emoji} {result_text}: {team1} vs {team2}",
                description=description,
                color=embed_color,
                timestamp=Timestamp.now(),
            )
            embed.add_field(
                name="Score Final",
                value=f"**{team1}** {score1} - {score2} **{team2}**",
                inline=False,
            )

            # Ajouter les détails de maps si disponibles
            if details:
                maps_text = self._format_match_details_maps(details)
                if maps_text:
                    embed.add_field(name="Détail des maps", value=maps_text, inline=False)
                # Top performers
                top_text = self._format_match_details_top_players(details)
                if top_text:
                    embed.add_field(name="Meilleurs joueurs", value=top_text, inline=False)

            embed.set_footer(text="Match terminé • Source: VLR.gg")

            await message.edit(
                content=f"{result_emoji} **Match terminé!** {result_emoji}",
                embeds=[embed],
            )
            logger.info(f"Match terminé: {team1} {score1}-{score2} {team2}")
        except Exception as e:
            logger.exception(f"Erreur gestion fin de match VLR.gg: {e}")

    # ── Récupération des données ─────────────────────────────────────

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

    # ── Construction des embeds ──────────────────────────────────────

    def _build_vlrgg_embeds(self, vlr_data: dict[str, Any], team: str) -> list[Embed]:
        """Construit les embeds Discord à partir des données VLR.gg."""
        embeds = []

        results = vlr_data.get("results", [])
        if results:
            past_embed = self._create_base_embed(
                f"Derniers matchs de {team}", footer_text="Source: VLR.gg"
            )
            for i, match in enumerate(results[:MAX_PAST_MATCHES]):
                field_data = self._format_vlrgg_result(match, team)
                past_embed.add_field(
                    name=field_data["name"], value=field_data["value"], inline=True
                )
                if (i + 1) % 2 != 0:
                    past_embed.add_field(**SPACER_FIELD)
            embeds.append(past_embed)

        live = vlr_data.get("live", [])
        if live:
            live_embed = self._create_base_embed(
                f"Match en cours de {team}", footer_text="Source: VLR.gg"
            )
            for match in live:
                field_data = self._format_vlrgg_live(match)
                live_embed.add_field(
                    name=field_data["name"], value=field_data["value"], inline=False
                )
            embeds.append(live_embed)

        upcoming = vlr_data.get("upcoming", [])
        if upcoming:
            upcoming_embed = self._create_base_embed(
                f"Prochains matchs de {team}",
                footer_text="Source: VLR.gg",
            )
            for i, match in enumerate(upcoming[:MAX_UPCOMING_MATCHES]):
                field_data = self._format_vlrgg_upcoming(match)
                upcoming_embed.add_field(
                    name=field_data["name"], value=field_data["value"], inline=True
                )
                if (i + 1) % 2 != 0:
                    upcoming_embed.add_field(**SPACER_FIELD)
            embeds.append(upcoming_embed)

        return embeds

    def _format_vlrgg_result(self, match: dict[str, Any], team: str) -> dict[str, str]:
        """Formate un résultat VLR.gg pour l'affichage."""
        team1 = match.get("team1", "???")
        team2 = match.get("team2", "???")
        score1 = match.get("score1", "?")
        score2 = match.get("score2", "?")
        time_completed = match.get("time_completed", "?")
        tournament = match.get("tournament_name", "")
        round_info = match.get("round_info", "")

        # Construire la description du tournoi
        event_line = tournament
        if round_info:
            event_line = f"{tournament} — {round_info}" if tournament else round_info

        # Utiliser le champ "result" direct de l'API si disponible
        result_api = match.get("result", "")
        if result_api == "win":
            resultat = "Gagné ✅"
        elif result_api == "loss":
            resultat = "Perdu ❌"
        else:
            # Fallback: calcul depuis les scores
            try:
                s1, s2 = int(score1), int(score2)
                team_lower = team.lower()
                is_team1 = team_lower in team1.lower()
                team_won = (is_team1 and s1 > s2) or (not is_team1 and s2 > s1)
                resultat = "Gagné ✅" if team_won else "Perdu ❌"
            except (ValueError, TypeError):
                resultat = ""

        # Déterminer qui a gagné pour le gras
        try:
            s1_int, s2_int = int(score1), int(score2)
            t1_won = s1_int > s2_int
            t2_won = s2_int > s1_int
        except (ValueError, TypeError):
            t1_won = t2_won = False

        # Noms en gras pour le gagnant
        t1_display = f"**{team1}**" if t1_won else team1
        t2_display = f"**{team2}**" if t2_won else team2
        sc1_display = f"**{score1}**" if t1_won else score1
        sc2_display = f"**{score2}**" if t2_won else score2

        # Scores par map
        maps_line = ""
        maps_data = match.get("maps", [])
        if maps_data:
            map_parts = []
            for m in maps_data:
                name = m.get("map", "?")[:3]  # Abréger le nom (Aby, Cor, Bin...)
                ms1, ms2 = m.get("score1", "?"), m.get("score2", "?")
                try:
                    ms1_int, ms2_int = int(ms1), int(ms2)
                    if ms1_int > ms2_int:
                        map_parts.append(f"{name} **{ms1}**-{ms2}")
                    elif ms2_int > ms1_int:
                        map_parts.append(f"{name} {ms1}-**{ms2}**")
                    else:
                        map_parts.append(f"{name} {ms1}-{ms2}")
                except (ValueError, TypeError):
                    map_parts.append(f"{name} {ms1}-{ms2}")
            maps_line = " | ".join(map_parts)

        value_parts = [event_line, maps_line, time_completed, resultat]
        value = "\n".join(p for p in value_parts if p)

        return {
            "name": f"{t1_display} {sc1_display}-{sc2_display} {t2_display}",
            "value": value,
        }

    def _format_vlrgg_live(self, match: dict[str, Any]) -> dict[str, str]:
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

    def _format_vlrgg_upcoming(self, match: dict[str, Any]) -> dict[str, str]:
        """Formate un match à venir VLR.gg pour l'affichage."""
        team1 = match.get("team1", "???")
        team2 = match.get("team2", "???")
        event = match.get("match_event", match.get("tournament_name", ""))
        round_info = match.get("round_info", "")
        time_display = match.get("time_completed", "?")

        # Si on a un unix_timestamp ou un timestamp classique, l'utiliser
        timestamp_str = match.get("unix_timestamp", "")
        ts = parse_vlrgg_timestamp(timestamp_str)
        if ts:
            time_display = format_discord_timestamp(ts, "R")
        elif match.get("time_until_match"):
            time_display = match["time_until_match"]

        event_line = event
        if round_info:
            event_line = f"{event} — {round_info}" if event else round_info

        return {
            "name": f"{team1} vs {team2}",
            "value": f"{time_display}\n{event_line}",
        }

    def _extract_vlrgg_ongoing(self, vlr_data: dict[str, Any]) -> dict[str, Any]:
        """Extrait les matchs en cours depuis les données VLR.gg."""
        ongoing: dict[str, Any] = {}
        for match in vlr_data.get("live", []):
            match_id = self._make_vlr_match_id(match)
            ongoing[match_id] = match
        return ongoing

    # ── Formatage des détails de match (depuis /match/details) ───────

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

            # Scores: format nested {"score": {"team1": 13, "team2": 9}}
            score_data = map_info.get("score", {})
            if isinstance(score_data, dict):
                t1_score = score_data.get("team1", "?")
                t2_score = score_data.get("team2", "?")
            else:
                t1_score = map_info.get("team1_score", "?")
                t2_score = map_info.get("team2_score", "?")

            # Mettre en gras le score du gagnant
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
        # Chercher les stats dans les maps
        all_players: dict[str, dict[str, Any]] = {}
        maps_data = details.get("maps", [])

        for map_info in maps_data:
            # Format réel: {"players": {"team1": [...], "team2": [...]}}
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

        # Trier par rating moyen
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

    # ── Persistance MongoDB ──────────────────────────────────────────

    def _live_col(self, server_id: str):
        return mongo_manager.get_guild_collection(server_id, "vlrgg_live")

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

    async def _restore_live_state(self, server_id: str, server_state: "ServerState") -> None:
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

    # ── Utilitaires ──────────────────────────────────────────────────

    @staticmethod
    def _make_vlr_match_id(match: dict[str, Any]) -> str:
        """Génère un ID unique pour un match VLR.gg."""
        # Préférer l'URL du match si disponible pour un ID plus stable
        match_url = match.get("match_page", "")
        url_id = extract_match_id_from_url(match_url)
        if url_id:
            return f"vlrgg_{url_id}"
        return f"vlrgg_{match.get('team1', '')}_{match.get('team2', '')}"

    def _create_base_embed(self, title: str, description: str = "", footer_text: str = "") -> Embed:
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

    @staticmethod
    def _format_game_score(score_1: int, score_2: int) -> str:
        """Formate le score d'une map avec le gagnant en gras."""
        if score_1 > score_2:
            return f"**{score_1}**-{score_2}"
        elif score_2 > score_1:
            return f"{score_1}-**{score_2}**"
        return f"{score_1}-{score_2}"

    @staticmethod
    def _add_alignment_field_if_needed(embed: Embed, count: int) -> None:
        """Ajoute un champ vide pour l'alignement si nécessaire."""
        if count % 2 != 0:
            embed.add_field(**SPACER_FIELD)
