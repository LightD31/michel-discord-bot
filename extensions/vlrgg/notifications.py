"""NotificationsMixin — match notification logic and background tasks."""

from typing import Any

from interactions import Embed, IntervalTrigger, Task, Timestamp

from src.vlrgg import (
    _clean_vlr_text,
    expand_round_name,
    extract_match_id_from_url,
    fetch_match_details,
)

from ._common import (
    DEFAULT_EMBED_COLOR,
    LIVE_EMBED_COLOR,
    LIVE_UPDATE_INTERVAL_MINUTES,
    SCHEDULE_INTERVAL_MINUTES,
    TeamState,
    logger,
)
from src.discord_ext.embeds import Colors


class NotificationsMixin:
    """Mixin providing match notification slash commands and background tasks."""

    # ── Scheduled tasks ───────────────────────────────────────────────────────

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

    # ── Per-team update logic ─────────────────────────────────────────────────

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

    # ── Match transition handling ─────────────────────────────────────────────

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

    # ── Live notifications ────────────────────────────────────────────────────

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
                score1 = (
                    str(teams_detail[0].get("score", "?")) if len(teams_detail) > 0 else "?"
                )
                score2 = (
                    str(teams_detail[1].get("score", "?")) if len(teams_detail) > 1 else "?"
                )
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
                event_obj = details["event"]
                series = _clean_vlr_text(event_obj.get("series", ""))
                full_name = _clean_vlr_text(event_obj.get("name", ""))
                if full_name and series and series in full_name:
                    event_name = full_name[: full_name.index(series)].strip()
                else:
                    event_name = full_name
                round_info = expand_round_name(series) if series else ""
            if not event_name:
                event_name = (match_data or {}).get("match_event", "") or (
                    match_data or {}
                ).get("tournament_name", "Tournoi")

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
