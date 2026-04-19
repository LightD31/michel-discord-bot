"""EmbedsMixin — embed-building methods for the vlrgg extension."""

from typing import Any

from interactions import Embed, Timestamp

from src.discord_ext.embeds import SPACER_FIELD, format_discord_timestamp
from features.vlrgg import parse_vlrgg_timestamp

from ._common import (
    DEFAULT_EMBED_COLOR,
    LIVE_EMBED_COLOR,
    MAX_PAST_MATCHES,
    MAX_UPCOMING_MATCHES,
)


class EmbedsMixin:
    """Mixin providing embed-building helpers for VLR.gg data."""

    # ── Top-level embed builder ───────────────────────────────────────────────

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

    # ── Per-match formatters ──────────────────────────────────────────────────

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

    # ── Base embed factory ────────────────────────────────────────────────────

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

    # ── Utility formatters ────────────────────────────────────────────────────

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
