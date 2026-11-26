"""NotificationsMixin — match notification logic and background tasks."""

from typing import Any

from interactions import IntervalTrigger, MessageFlags, Task

from features.vlrgg import (
    fetch_live_matches,
    fetch_match_details,
    is_final,
    match_id_of,
    not_started,
)
from src.core import log_context
from src.discord_ext.links import shorten_embeds
from src.discord_ext.messages import (
    edit_message_if_changed,
    fetch_or_create_persistent_message,
    forget_persistent_message,
)

from ._base import VlrggBase
from ._common import (
    FINALIZE_GIVE_UP_ATTEMPTS,
    FINALIZE_MAX_ATTEMPTS,
    FINALIZE_SLOW_EVERY,
    LIVE_UPDATE_INTERVAL_MINUTES,
    SCHEDULE_INTERVAL_MINUTES,
    VLRGG,
    PendingFinal,
    ScheduleDisplay,
    TeamState,
    logger,
)
from .embeds import fit_v2


def _live_heading(team: str) -> str:
    return f"🔴 **Match en direct — {team}!**"


def _waiting_heading(team: str) -> str:
    return (
        f"⏳ **Match retardé — {team}** : il n'a pas encore commencé. "
        "Ce message reprendra au coup d'envoi."
    )


class NotificationsMixin(VlrggBase):
    """Mixin providing match notification background tasks."""

    # ── Scheduled tasks ───────────────────────────────────────────────────────

    @Task.create(IntervalTrigger(minutes=SCHEDULE_INTERVAL_MINUTES))
    async def schedule(self) -> None:
        """Tâche planifiée pour mettre à jour les plannings de toutes les équipes."""
        logger.debug("Exécution de la tâche schedule")
        for server_id, server_state in self._servers.items():
            with log_context.scope(guild_id=server_id):
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
            with log_context.scope(guild_id=server_id):
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
        display = ScheduleDisplay.from_config(VLRGG.get(team_state.server_id))
        embeds, ongoing_matches, vlr_data = await self._fetch_team_schedule(tc, display)
        await self._handle_match_transitions(ongoing_matches, team_state)
        message = await fetch_or_create_persistent_message(
            self.bot,
            guild_id=team_state.server_id,
            slot=tc.slot,
            channel_id=tc.channel_id,
            label=f"Planning {tc.name}",
            initial_content=f"Initialisation du planning de {tc.name}…",
            pin=tc.pin,
            logger=logger,
        )
        if message and embeds:
            embeds = await shorten_embeds(embeds, guild_id=team_state.server_id, tags=["vlrgg"])
            try:
                await edit_message_if_changed(
                    message,
                    components=fit_v2(embeds),
                    flags=MessageFlags.IS_COMPONENTS_V2,
                    logger=logger,
                )
            except Exception:
                # Deleted from Discord, say: resolved again (or reposted) next cycle.
                forget_persistent_message(team_state.server_id, tc.slot)
                raise
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
                content=_live_heading(team_state.team_config.name),
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
        """Gère la fin d'un match VLR.gg — prépare le résumé final du message live.

        La référence persistée reste en base jusqu'au résumé : un redémarrage
        pendant l'attente retrouve le message au lieu d'en poster un second.
        """
        message = team_state.live_messages.pop(match_id, None)
        match_data = team_state.ongoing_matches.pop(match_id, None)
        if not message:
            await self._delete_live_match(team_state.server_id, match_id)
            return
        team_state.ending_matches[match_id] = PendingFinal(message, match_data or {})
        await self._try_finalize_match(match_id, team_state)

    async def _resume_live_match(
        self, match_id: str, match: dict[str, Any], team_state: TeamState
    ) -> None:
        """Le match réapparaît en direct avant son résumé final : on reprend son message.

        Évite qu'un trou ponctuel dans /v2/match?q=live_score clôture le match
        puis le réannonce comme un nouveau, et qu'un match retardé après son heure
        en poste un second à son vrai coup d'envoi. Tant que la page du match
        affiche un compte à rebours, il n'a pas commencé : il reste en attente.
        """
        pending = team_state.ending_matches.get(match_id)
        if pending is None:
            return
        if not_started(await self._live_details(match)):
            return
        team_state.ending_matches.pop(match_id, None)
        name = team_state.team_config.name
        logger.info(f"Match {match_id} de nouveau en direct pour {name}")
        team_state.live_messages[match_id] = pending.message
        team_state.ongoing_matches[match_id] = match
        try:
            await edit_message_if_changed(
                pending.message, content=_live_heading(name), ignore_timestamp=True, logger=logger
            )
        except Exception as e:
            logger.warning(f"Impossible de remettre en direct le message de {match_id}: {e}")
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

        Un match sorti de ``/v2/match?q=live_score`` n'est pas forcément fini : la
        liste le montre à son heure prévue même s'il est retardé, et peut le perdre
        un moment. Sa page tranche :

        - « final » : le résumé remplace le message live ;
        - un compte à rebours : le match est retardé, le message le dit
          et attend son vrai coup d'envoi, où :meth:`_resume_live_match` le reprend ;
        - sinon (encore « live », ou page indisponible) : on retente à chaque
          cycle live pendant ``FINALIZE_MAX_ATTEMPTS`` cycles, puis tous les
          ``FINALIZE_SLOW_EVERY`` cycles, jusqu'à ``FINALIZE_GIVE_UP_ATTEMPTS`` ;
          s'il reparaît en direct entre-temps, :meth:`_resume_live_match` reprend
          son message. Aucun résultat n'est publié sans page « final ».
        """
        pending = team_state.ending_matches.get(match_id)
        if pending is None:
            return
        pending.attempts += 1
        slow = pending.attempts > FINALIZE_MAX_ATTEMPTS
        last = pending.attempts >= FINALIZE_GIVE_UP_ATTEMPTS
        if slow and not last and pending.attempts % FINALIZE_SLOW_EVERY:
            return
        details = await self._live_details(pending.match_data)
        tc = team_state.team_config

        if not_started(details):
            # Retardé : le message attend le vrai coup d'envoi, qui le reprendra
            logger.info(f"Match {match_id} ({tc.name}) pas encore commencé : message en attente")
            try:
                await edit_message_if_changed(
                    pending.message,
                    content=_waiting_heading(tc.name),
                    ignore_timestamp=True,
                    logger=logger,
                )
            except Exception as e:
                logger.warning(f"Impossible de mettre en attente le message de {match_id}: {e}")
            return

        if is_final(details):
            await self._close_pending(match_id, team_state)
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
            return

        if last:
            await self._close_pending(match_id, team_state)
            logger.warning(f"Match {match_id} ({tc.name}) jamais affiché « final » : suivi arrêté")
            try:
                await edit_message_if_changed(
                    pending.message,
                    content=(
                        f"⏸️ **Match plus suivi — {tc.name}** : VLR.gg ne le montre plus "
                        "en direct, et sans résultat final."
                    ),
                    ignore_timestamp=True,
                    logger=logger,
                )
            except Exception as e:
                logger.warning(f"Impossible de clore le message live de {match_id}: {e}")

    async def _close_pending(self, match_id: str, team_state: TeamState) -> None:
        """Fin du suivi d'un match terminé : plus en attente, plus en base."""
        team_state.ending_matches.pop(match_id, None)
        await self._delete_live_match(team_state.server_id, match_id)
