"""NotificationsMixin — match notification logic and background tasks."""

from typing import Any

from interactions import IntervalTrigger, Task

from features.vlrgg import fetch_live_matches, fetch_match_details, is_final, match_id_of
from src.discord_ext.links import shorten_embeds
from src.discord_ext.messages import edit_message_if_changed

from ._common import (
    FINALIZE_MAX_ATTEMPTS,
    LIVE_UPDATE_INTERVAL_MINUTES,
    SCHEDULE_INTERVAL_MINUTES,
    PendingFinal,
    TeamState,
    logger,
)


class NotificationsMixin:
    """Mixin providing match notification background tasks."""

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
                    logger.exception(f"Erreur schedule pour {team_name} (serveur {server_id}): {e}")

    @Task.create(IntervalTrigger(minutes=LIVE_UPDATE_INTERVAL_MINUTES))
    async def live_update(self) -> None:
        """Tâche planifiée pour mettre à jour les scores des matchs en cours."""
        for server_id, server_state in self._servers.items():
            for team_name, team_state in server_state.teams.items():
                if not team_state.ongoing_matches and not team_state.ending_matches:
                    continue
                try:
                    await self._update_team_live(team_state)
                except Exception as e:
                    logger.exception(
                        f"Erreur live_update pour {team_name} (serveur {server_id}): {e}"
                    )

    # ── Per-team update logic ─────────────────────────────────────────────────

    async def _update_team_schedule(self, team_state: TeamState) -> None:
        """Met à jour le planning d'une équipe, ses notifications live et ses rappels."""
        tc = team_state.team_config
        embeds, ongoing_matches, vlr_data = await self._fetch_team_schedule(tc)
        await self._handle_match_transitions(ongoing_matches, team_state)
        if team_state.schedule_message and embeds:
            embeds = await shorten_embeds(embeds, tags=["vlrgg"])
            await edit_message_if_changed(
                team_state.schedule_message, embeds=embeds, ignore_timestamp=True, logger=logger
            )
        if vlr_data:
            await self._send_due_reminders(team_state, vlr_data)

    async def _update_team_live(self, team_state: TeamState) -> None:
        """Met à jour les scores live d'une équipe via VLR.gg.

        Ne lit que /v2/match?q=live_score et les détails des matchs suivis : pas
        d'historique ni de calendrier, que la tâche schedule rafraîchit déjà.
        """
        tc = team_state.team_config
        logger.debug(
            f"Mise à jour live de {len(team_state.ongoing_matches)} match(s) pour {tc.name}"
        )
        try:
            live_by_id = {self._make_vlr_match_id(m): m for m in await fetch_live_matches()}
            for match_id in list(team_state.ongoing_matches):
                segment = live_by_id.get(match_id)
                if segment is None:
                    await self._handle_vlr_match_ended(match_id, team_state)
                    continue
                team_state.ongoing_matches[match_id] = segment
                await self._update_vlrgg_live_message(segment, match_id, team_state)
            for match_id in list(team_state.ending_matches):
                if match_id in live_by_id:
                    await self._resume_live_match(match_id, live_by_id[match_id], team_state)
                else:
                    await self._try_finalize_match(match_id, team_state)
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
            if match_id in team_state.ending_matches:
                await self._resume_live_match(match_id, match, team_state)
            elif match_id not in team_state.ongoing_matches:
                logger.info(f"Nouveau match détecté pour {tc.name}: {match_id}")
                await self._send_vlrgg_match_started_notification(match, team_state)
                team_state.ongoing_matches[match_id] = match

        # Matchs terminés : même traitement que lorsque la tâche live le remarque,
        # pour que le message live reçoive son résumé final.
        finished = [mid for mid in team_state.ongoing_matches if mid not in ongoing_matches]
        for match_id in finished:
            logger.info(f"Match terminé détecté pour {tc.name}: {match_id}")
            await self._handle_vlr_match_ended(match_id, team_state)

    # ── Live notifications ────────────────────────────────────────────────────

    @staticmethod
    async def _live_details(match: dict[str, Any]) -> dict[str, Any]:
        vlr_match_id = match_id_of(match)
        if not vlr_match_id:
            return {}
        try:
            return await fetch_match_details(vlr_match_id)
        except Exception as e:
            logger.warning(f"Impossible de récupérer les détails du match {vlr_match_id}: {e}")
            return {}

    async def _send_vlrgg_match_started_notification(
        self, match: dict[str, Any], team_state: TeamState
    ) -> None:
        """Envoie une notification VLR.gg quand un match commence."""
        channel = team_state.notification_channel
        if not channel:
            return

        try:
            details = await self._live_details(match)
            embed = self._build_live_embed(match, details, just_started=True)
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
            logger.info(
                f"Notification VLR.gg envoyée pour {match.get('team1')} vs {match.get('team2')}"
            )
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
            details = await self._live_details(match)
            embed = self._build_live_embed(match, details)
            await edit_message_if_changed(
                message, embeds=[embed], ignore_timestamp=True, logger=logger
            )
            logger.debug(
                f"Message live VLR.gg mis à jour pour {match_id}: "
                f"{match.get('score1')}-{match.get('score2')}"
            )
        except Exception as e:
            logger.exception(f"Erreur mise à jour VLR.gg message live: {e}")

    async def _handle_vlr_match_ended(self, match_id: str, team_state: TeamState) -> None:
        """Gère la fin d'un match VLR.gg — prépare le résumé final du message live."""
        message = team_state.live_messages.pop(match_id, None)
        match_data = team_state.ongoing_matches.pop(match_id, None)
        await self._delete_live_match(team_state.server_id, match_id)
        if not message:
            return
        team_state.ending_matches[match_id] = PendingFinal(message, match_data or {})
        await self._try_finalize_match(match_id, team_state)

    async def _resume_live_match(
        self, match_id: str, match: dict[str, Any], team_state: TeamState
    ) -> None:
        """Le match réapparaît en direct avant son résumé final : on reprend son message.

        Évite qu'un trou ponctuel dans /v2/match?q=live_score clôture le match
        puis le réannonce comme un nouveau.
        """
        pending = team_state.ending_matches.pop(match_id, None)
        if pending is None:
            return
        logger.info(f"Match {match_id} de nouveau en direct pour {team_state.team_config.name}")
        team_state.live_messages[match_id] = pending.message
        team_state.ongoing_matches[match_id] = match
        await self._save_live_match(
            team_state.server_id,
            team_state.team_config.name,
            match_id,
            match,
            str(pending.message.channel.id),
            str(pending.message.id),
        )

    async def _try_finalize_match(self, match_id: str, team_state: TeamState) -> None:
        """Édite le message live avec le résumé final, dès que VLR.gg l'affiche « final ».

        Juste après la fin, la page du match peut encore être servie « LIVE » par
        le cache de l'API : on retente au cycle live suivant, au plus
        ``FINALIZE_MAX_ATTEMPTS`` fois, puis on publie ce qu'on a.
        """
        pending = team_state.ending_matches.get(match_id)
        if pending is None:
            return
        pending.attempts += 1
        details = await self._live_details(pending.match_data)
        if not is_final(details) and pending.attempts < FINALIZE_MAX_ATTEMPTS:
            return
        team_state.ending_matches.pop(match_id, None)

        tc = team_state.team_config
        try:
            embed, content = self._build_final_embed(pending.match_data, details, tc)
            await edit_message_if_changed(
                pending.message,
                content=content,
                embeds=[embed],
                ignore_timestamp=True,
                logger=logger,
            )
            logger.info(f"Match terminé ({tc.name}): {embed.title}")
        except Exception as e:
            logger.exception(f"Erreur gestion fin de match VLR.gg: {e}")
