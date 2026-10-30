"""RemindersMixin — rappel posté quelques minutes avant le début d'un match."""

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from interactions import Embed, Timestamp

from features.vlrgg import (
    MapRecord,
    VlrggReminderRepository,
    compare_map_pools,
    due_for_reminder,
    fetch_match_details,
    fetch_team_map_stats,
    head_to_head,
    map_pool,
    team_side,
)
from src.core.errors import DatabaseError
from src.discord_ext.embeds import format_discord_timestamp

from ._common import DEFAULT_EMBED_COLOR, MAP_POOL_DAYS, TeamState, logger

MAX_HEAD_TO_HEAD = 5
MAX_POOL_MAPS = 7


def _pool_cell(tag: str, record: MapRecord | None) -> str:
    if record is None:
        return f"{tag} —"
    return f"{tag} {record.win_pct}% ({record.games})"


class RemindersMixin:
    """Mixin posting a pre-match reminder in the notification channel."""

    async def _send_due_reminders(self, team_state: TeamState, vlr_data: dict[str, Any]) -> None:
        """Poste un rappel pour chaque match qui commence dans ``reminder_minutes``."""
        channel = team_state.notification_channel
        if not channel or team_state.reminder_minutes <= 0:
            return

        due = due_for_reminder(
            vlr_data.get("upcoming", []),
            now=time.time(),
            window_seconds=team_state.reminder_minutes * 60,
        )
        if not due:
            return

        tc = team_state.team_config
        repo = VlrggReminderRepository(team_state.server_id)
        for match in due:
            match_id = str(match.get("match_id") or "")
            if not match_id:
                continue
            try:
                if await repo.was_sent(match_id):
                    continue
                embed = await self._build_reminder_embed(match, vlr_data, team_state)
                await channel.send(
                    content=(
                        f"⏰ **{tc.name}** joue {self._kickoff(match, 'R')} : "
                        f"{match.get('team1')} vs {match.get('team2')}"
                    ),
                    embeds=[embed],
                )
                await repo.mark_sent(match_id, tc.name)
                logger.info(f"Rappel VLR.gg envoyé pour {tc.name} (match {match_id})")
            except DatabaseError as e:
                logger.warning(f"Rappel VLR.gg ignoré pour {match_id}: {e}")
            except Exception as e:
                logger.exception(f"Erreur rappel VLR.gg pour {match_id}: {e}")

    @staticmethod
    def _kickoff(match: dict[str, Any], style: str) -> str:
        return format_discord_timestamp(datetime.fromtimestamp(match["kickoff"], UTC), style)

    async def _build_reminder_embed(
        self, match: dict[str, Any], vlr_data: dict[str, Any], team_state: TeamState
    ) -> Embed:
        """Événement, heure, face-à-face récents et maps des deux équipes."""
        tc = team_state.team_config
        team1, team2 = match.get("team1", "???"), match.get("team2", "???")

        event_line = match.get("match_event", "")
        if match.get("round_info"):
            sep = " — " if event_line else ""
            event_line = f"{event_line}{sep}{match['round_info']}"
        description = "\n".join(
            p
            for p in (
                f"**{event_line}**" if event_line else "",
                f"Début {self._kickoff(match, 'R')} ({self._kickoff(match, 'f')})",
            )
            if p
        )
        embed = Embed(
            title=f"⏰ {team1} vs {team2}",
            description=description,
            color=DEFAULT_EMBED_COLOR,
            timestamp=Timestamp.now(),
        )
        logo = str((vlr_data.get("profile") or {}).get("logo") or "")
        if logo.startswith("http"):
            embed.set_thumbnail(url=logo)

        # Les détails donnent les deux équipes avec leur ID, quel que soit l'ordre
        details = await fetch_match_details(str(match["match_id"]))
        teams = details.get("teams") or []
        side = team_side(details, tc.vlr_team_id)
        if side is not None and len(teams) >= 2:
            ours, theirs = teams[side], teams[1 - side]
        else:
            ours, theirs = {"name": team1}, {"name": team2}

        h2h_lines = []
        opponent = str(theirs.get("name") or "")
        for past in head_to_head(vlr_data.get("results", []), opponent, MAX_HEAD_TO_HEAD):
            won = past.get("result") == "win"
            h2h_lines.append(
                f"{'✅' if won else '❌'} {past.get('score1')}-{past.get('score2')} · "
                f"{past.get('time_completed', '')}"
            )
        if h2h_lines:
            embed.add_field(name="Face-à-face récents", value="\n".join(h2h_lines), inline=False)

        pool_text = await self._map_pool_comparison(tc.vlr_team_id, ours, theirs)
        if pool_text:
            embed.add_field(
                name=f"Maps jouées ({MAP_POOL_DAYS} derniers jours)",
                value=pool_text,
                inline=False,
            )
        embed.set_footer(text="Source: VLR.gg")
        return embed

    async def _map_pool_comparison(
        self, team_id: str | None, ours: dict[str, Any], theirs: dict[str, Any]
    ) -> str:
        """« **Haven** — M8GC 57% (14) · SRG 40% (5) », une ligne par map."""
        opponent_id = str(theirs.get("id") or "")
        if not team_id or not opponent_id:
            return ""
        since = (datetime.now(UTC) - timedelta(days=MAP_POOL_DAYS)).date()
        stats_ours, stats_theirs = await asyncio.gather(
            fetch_team_map_stats(team_id, since), fetch_team_map_stats(opponent_id, since)
        )
        tag_ours = str(ours.get("tag") or ours.get("name") or "")
        tag_theirs = str(theirs.get("tag") or theirs.get("name") or "")
        rows = compare_map_pools(map_pool(stats_ours), map_pool(stats_theirs))[:MAX_POOL_MAPS]
        return "\n".join(
            f"**{name}** — {_pool_cell(tag_ours, a)} · {_pool_cell(tag_theirs, b)}"
            for name, a, b in rows
        )
