"""EmbedsMixin — embed-building methods for the vlrgg extension."""

from datetime import UTC, datetime
from typing import Any

from interactions import ContainerComponent, Embed, Timestamp

from features.vlrgg import (
    BracketMatch,
    BracketRound,
    Competition,
    StandingTable,
    event_and_series,
    expand_round_name,
    live_round_score,
    map_scores,
    round_label,
    same_team_name,
    team_side,
    top_players,
    veto_line,
)
from src.core.text import format_discord_timestamp
from src.discord_ext.look import SPACER_FIELD, V2_TEXT_LIMIT, Kind, card, embeds_to_v2, sign

from ._base import VlrggBase
from ._common import ScheduleDisplay, TeamConfig

# Le planning entier tient dans un message V2 (4000 caractères de texte en tout) :
# la compétition en prend au plus ceci, les tours les plus anciens cédant la place.
COMPETITION_TEXT_BUDGET = 1600
FIELD_LIMIT = 1024


def _media_url(url: str) -> str:
    """Les logos de VLR.gg arrivent parfois sans schéma ("//owcdn.net/...")."""
    if url.startswith("//"):
        return f"https:{url}"
    return url if url.startswith("http") else ""


def _ts(epoch: int, style: str) -> str:
    return format_discord_timestamp(datetime.fromtimestamp(epoch, UTC), style)


def _text_size(component: Any) -> int:
    """Caractères de texte d'un composant V2 et de ceux qu'il contient."""
    own = len(getattr(component, "content", None) or "")
    return own + sum(_text_size(c) for c in getattr(component, "components", None) or [])


def fit_v2(embeds: list[Embed], limit: int = V2_TEXT_LIMIT) -> list[ContainerComponent]:
    """Les cartes V2 du planning, dans la limite de texte d'un message Discord.

    Tant que le tout dépasse *limit*, la carte la plus fournie perd son dernier
    champ (le match le plus ancien, ou le plus lointain) ; elle en garde un.
    """
    cards = embeds_to_v2(embeds)
    while sum(_text_size(c) for c in cards) > limit:
        fullest = max(embeds, key=lambda e: len(e.fields or []), default=None)
        if fullest is None or len(fullest.fields or []) <= 1:
            break
        fullest.fields.pop()
        if fullest.fields and fullest.fields[-1].name == SPACER_FIELD["name"]:
            fullest.fields.pop()
        cards = embeds_to_v2(embeds)
    return cards


def _fit_lines(lines: list[tuple[bool, str]], limit: int) -> str:
    """Les lignes qui tiennent dans *limit*, celle de l'équipe suivie gardée en dernier recours."""
    kept: list[str] = []
    total = 0
    for _, line in lines:
        if total + len(line) + 1 > limit - 80:  # place pour « … » et la ligne de l'équipe
            break
        kept.append(line)
        total += len(line) + 1
    if len(kept) < len(lines):
        kept.append("…")
        ours = [line for flag, line in lines[len(kept) - 1 :] if flag]
        kept.extend(ours[:1])
    return "\n".join(kept)[:limit]


class EmbedsMixin(VlrggBase):
    """Mixin providing embed-building helpers for VLR.gg data."""

    # ── Top-level embed builder ───────────────────────────────────────────────

    def _build_vlrgg_embeds(
        self,
        vlr_data: dict[str, Any],
        team: str,
        display: ScheduleDisplay,
        competition: Competition | None = None,
    ) -> list[Embed]:
        """Construit les embeds Discord à partir des données VLR.gg."""
        embeds = []
        profile = vlr_data.get("profile") or {}
        logo = _media_url(str(profile.get("logo") or ""))

        results = vlr_data.get("results", [])[: display.past]
        if results:
            past_embed = self._create_base_embed(
                f"Derniers matchs de {team}",
                description=self._rank_line(profile),
                footer_text="Source : VLR.gg",
            )
            if logo:
                past_embed.set_thumbnail(url=logo)
            for i, match in enumerate(results):
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
                f"Match en cours de {team}", footer_text="Source : VLR.gg"
            )
            for match in live:
                field_data = self._format_vlrgg_live(match)
                live_embed.add_field(
                    name=field_data["name"], value=field_data["value"], inline=False
                )
            embeds.append(live_embed)

        upcoming = vlr_data.get("upcoming", [])[: display.upcoming]
        if upcoming:
            upcoming_embed = self._create_base_embed(
                f"Prochains matchs de {team}",
                footer_text="Source : VLR.gg",
            )
            for i, match in enumerate(upcoming):
                field_data = self._format_vlrgg_upcoming(match)
                upcoming_embed.add_field(
                    name=field_data["name"], value=field_data["value"], inline=True
                )
                if (i + 1) % 2 != 0:
                    upcoming_embed.add_field(**SPACER_FIELD)
            embeds.append(upcoming_embed)

        if competition is not None:
            official = str(profile.get("name") or team)
            embed = self._build_competition_embed(competition, official)
            if embed is not None:
                embeds.append(embed)

        return embeds

    # ── Compétition en cours ──────────────────────────────────────────────────

    def _build_competition_embed(self, competition: Competition, team: str) -> Embed | None:
        """Classement ou arbre de la phase que joue l'équipe, None s'il n'y a rien à montrer."""
        if competition.view == "standings":
            fields = [
                self._standings_field(t, team, len(competition.tables) > 1)
                for t in competition.tables
            ]
            view_label = "Classement"
        else:
            fields = self._bracket_fields(competition.rounds, team)
            view_label = "Arbre"
        if not fields:
            return None
        stage = expand_round_name(competition.stage)
        link = f"[VLR.gg]({competition.url})" if competition.url else ""
        embed = card(
            "moduleVlrgg",
            title=f"🏆 {competition.name or stage or 'Compétition'}",
            # Lien dans la description : un titre lié s'affiche en markdown brut en V2
            description=" · ".join(p for p in (stage, view_label, link) if p),
            timestamp=Timestamp.now(),
        )
        for name, value in fields:
            embed.add_field(name=name, value=value, inline=False)
        logo = _media_url(competition.logo)
        if logo:
            embed.set_thumbnail(url=logo)
        sign(embed, "moduleVlrgg", "Source : VLR.gg")
        return embed

    @staticmethod
    def _standings_field(table: StandingTable, team: str, titled: bool) -> tuple[str, str]:
        """Une table : rang, équipe (la suivie en gras), bilan, maps, différence de rounds."""
        lines = []
        for row in table.rows:
            ours = same_team_name(team, row.team)
            name = f"**{row.team}** ⭐" if ours else row.team
            stats = " · ".join(
                p
                for p in (
                    f"{row.wins}-{row.losses}",
                    f"maps {row.maps}" if row.maps else "",
                    f"Δ {row.rounds_diff}" if row.rounds_diff else "",
                )
                if p
            )
            lines.append((ours, f"`{row.rank:>2}.` {name} — {stats}"))
        return (table.title if titled else "\u200b", _fit_lines(lines, FIELD_LIMIT))

    def _bracket_fields(self, rounds: list[BracketRound], team: str) -> list[tuple[str, str]]:
        """Un champ par tour ; au-delà du budget, les tours joués sans l'équipe partent d'abord."""
        fields = [
            (round_label(r.name), [self._bracket_line(m, team) for m in r.matches], r)
            for r in rounds
        ]

        def size() -> int:
            return sum(
                len(name) + sum(len(line) + 1 for line in lines) for name, lines, _ in fields
            )

        def removable(rnd: BracketRound) -> bool:
            return all(m.played for m in rnd.matches) and not any(
                same_team_name(team, m.team1) or same_team_name(team, m.team2) for m in rnd.matches
            )

        for keep_ours in (True, False):
            for entry in list(fields):
                if size() <= COMPETITION_TEXT_BUDGET or len(fields) == 1:
                    break
                if not keep_ours or removable(entry[2]):
                    fields.remove(entry)
        return [(name, "\n".join(lines)[:FIELD_LIMIT]) for name, lines, _ in fields]

    def _bracket_line(self, match: BracketMatch, team: str) -> str:
        """« **GIANTX** 2-0 Pixel Lumina », « 🔴 A 1-0 B », « A vs B »."""
        ours = same_team_name(team, match.team1) or same_team_name(team, match.team2)
        star = " ⭐" if ours else ""
        t1 = f"**{match.team1}**" if match.winner == 0 else match.team1
        t2 = f"**{match.team2}**" if match.winner == 1 else match.team2
        if match.live:
            return f"🔴 {match.team1} {match.score1}-{match.score2} {match.team2}{star}"
        if match.played:
            score = self._format_score(match.score1, match.score2)
            return f"{t1} {score} {t2}{star}"
        return f"{match.team1} vs {match.team2}{star}"

    @staticmethod
    def _rank_line(profile: dict[str, Any]) -> str:
        """« #16 Europe · 1734 pts » depuis le profil, vide si VLR n'en publie pas."""
        rating = profile.get("rating") or {}
        rank = str(rating.get("rank") or "").strip()
        if not rank or rank.upper() == "N/A":
            return ""
        parts = [f"#{rank} {profile.get('country_name') or ''}".strip()]
        points = str(rating.get("rating") or "").strip()
        if points and points.upper() != "N/A":
            parts.append(f"{points} pts")
        return "🏆 Classement VLR.gg : " + " · ".join(parts)

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

        # Champ "result" de l'API ; l'équipe suivie est toujours team1 dans l'historique
        result_api = match.get("result", "")
        if result_api == "win":
            resultat = "Gagné ✅"
        elif result_api == "loss":
            resultat = "Perdu ❌"
        else:
            try:
                resultat = "Gagné ✅" if int(score1) > int(score2) else "Perdu ❌"
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
                map_parts.append(f"{name} {self._format_score(m.get('score1'), m.get('score2'))}")
            maps_line = " | ".join(map_parts)

        match_page = match.get("match_page", "")
        if match_page:
            resultat = (
                f"{resultat} · [VLR.gg]({match_page})" if resultat else f"[VLR.gg]({match_page})"
            )

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
        event = match.get("match_event", match.get("match_series", ""))

        value = f"**{event}**\n"
        current = self._current_map_text(match)
        if current:
            value += f"Map actuelle: {current}"

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

        kickoff = match.get("kickoff")
        if isinstance(kickoff, int):
            if match.get("kickoff_precise"):
                time_display = f"{_ts(kickoff, 'R')} · {_ts(kickoff, 'f')}"
            else:
                time_display = _ts(kickoff, "D")
        else:
            time_display = "Date à confirmer"

        event_line = event
        if round_info:
            event_line = f"{event} — {round_info}" if event else round_info

        return {
            "name": f"{team1} vs {team2}",
            "value": "\n".join(p for p in (time_display, event_line) if p),
        }

    # ── Live / final notifications ────────────────────────────────────────────

    @staticmethod
    def _current_map_text(match: dict[str, Any]) -> str:
        """« Summit — 2-3 » (rounds de la map en cours) depuis un segment live_score."""
        current_map = str(match.get("current_map") or "").strip()
        if current_map.lower() in ("", "unknown", "tbd"):
            current_map = ""
        score = live_round_score(match)
        rounds = f"{score[0]}-{score[1]}" if score else ""
        if current_map and rounds:
            return f"{current_map} — {rounds}"
        return current_map or rounds

    def _build_live_embed(
        self, match: dict[str, Any], details: dict[str, Any] | None, *, just_started: bool = False
    ) -> Embed:
        """Embed du message live : score, map en cours, maps jouées, veto."""
        details = details or {}
        team1 = match.get("team1", "???")
        team2 = match.get("team2", "???")
        score1 = match.get("score1", "0") or "0"
        score2 = match.get("score2", "0") or "0"

        event, series = event_and_series(details)
        event = event or match.get("match_event", "") or "Tournoi"
        series = series or match.get("match_series", "")
        description = f"**{event}**"
        if series:
            description += f"\n{expand_round_name(series)}"
        if just_started:
            description += "\n\nLe match vient de commencer!"

        embed = card(
            "moduleVlrgg",
            "success",
            title=f"🔴 LIVE: {team1} vs {team2}",
            description=description,
            timestamp=Timestamp.now(),
        )
        embed.add_field(
            name="Score", value=f"**{team1}** {score1} - {score2} **{team2}**", inline=False
        )
        current = self._current_map_text(match)
        if current:
            embed.add_field(name="Map actuelle", value=current, inline=False)
        maps_text = self._maps_text(details)
        if maps_text:
            embed.add_field(name="Maps", value=maps_text, inline=False)
        veto = veto_line(details)
        if veto:
            embed.add_field(name="Veto", value=veto, inline=False)
        logo = self._event_logo(details) or _media_url(str(match.get("team1_logo") or ""))
        if logo:
            embed.set_thumbnail(url=logo)
        sign(embed, "moduleVlrgg", "Mise à jour automatique · Source : VLR.gg")
        return embed

    def _build_final_embed(
        self, match_data: dict[str, Any], details: dict[str, Any] | None, tc: TeamConfig
    ) -> tuple[Embed, str]:
        """Embed et texte du message live une fois le match terminé."""
        details = details or {}
        teams = details.get("teams") or []
        if len(teams) >= 2:
            team1 = teams[0].get("name") or "???"
            team2 = teams[1].get("name") or "???"
            score1 = str(teams[0].get("score") or "?")
            score2 = str(teams[1].get("score") or "?")
        else:
            team1 = match_data.get("team1", "???")
            team2 = match_data.get("team2", "???")
            score1 = match_data.get("score1", "?")
            score2 = match_data.get("score2", "?")

        team_won: bool | None = None
        side = team_side(details, tc.vlr_team_id)
        if side is not None and any(t.get("is_winner") for t in teams[:2]):
            team_won = bool(teams[side].get("is_winner"))
        else:
            try:
                s1, s2 = int(score1), int(score2)
                if same_team_name(tc.name, team1):
                    team_won = s1 > s2
                elif same_team_name(tc.name, team2):
                    team_won = s2 > s1
            except (ValueError, TypeError):
                pass

        kind: Kind
        if team_won is True:
            result_emoji, result_text, kind = "🎉", "VICTOIRE", "success"
        elif team_won is False:
            result_emoji, result_text, kind = "😢", "DÉFAITE", "error"
        else:
            result_emoji, result_text, kind = "🏁", "TERMINÉ", "info"

        event_name, series = event_and_series(details)
        if not event_name:
            event_name = match_data.get("match_event", "") or match_data.get(
                "tournament_name", "Tournoi"
            )
        description = f"**{event_name}**"
        if series:
            description += f"\n{expand_round_name(series)}"

        embed = card(
            "moduleVlrgg",
            kind,
            title=f"{result_emoji} {result_text}: {team1} vs {team2}",
            description=description,
            timestamp=Timestamp.now(),
        )
        embed.add_field(
            name="Score Final", value=f"**{team1}** {score1} - {score2} **{team2}**", inline=False
        )
        self._add_details_fields(embed, details)
        logo = self._event_logo(details)
        if logo:
            embed.set_thumbnail(url=logo)
        sign(embed, "moduleVlrgg", "Match terminé · Source : VLR.gg")
        return embed, f"{result_emoji} **Match terminé!** {result_emoji}"

    def _add_details_fields(self, embed: Embed, details: dict[str, Any]) -> None:
        """Ajoute maps (avec picks), veto et meilleurs joueurs depuis les détails."""
        maps_text = self._maps_text(details)
        if maps_text:
            embed.add_field(name="Détail des maps", value=maps_text, inline=False)
        veto = veto_line(details)
        if veto:
            embed.add_field(name="Veto", value=veto, inline=False)
        top_text = self._top_players_text(details)
        if top_text:
            embed.add_field(name="Meilleurs joueurs", value=top_text, inline=False)

    def _maps_text(self, details: dict[str, Any]) -> str:
        """Une ligne par map : nom, score (gagnant en gras), pick, durée."""
        lines = []
        for m in map_scores(details):
            extras = []
            if m.picked_by:
                extras.append(f"pick {m.picked_by}")
            if m.duration:
                extras.append(m.duration)
            suffix = f" ({' · '.join(extras)})" if extras else ""
            lines.append(f"**{m.name}**: {self._format_score(m.score1, m.score2)}{suffix}")
        return "\n".join(lines)

    @staticmethod
    def _top_players_text(details: dict[str, Any], n: int = 3) -> str:
        lines = []
        for p in top_players(details, n):
            agents = f" ({', '.join(p.agents)})" if p.agents else ""
            lines.append(
                f"**{p.name}**{agents} — {p.rating:.2f} · {p.acs:.0f} ACS · "
                f"{p.kills}/{p.deaths}/{p.assists}"
            )
        return "\n".join(lines)

    @staticmethod
    def _event_logo(details: dict[str, Any]) -> str:
        event = details.get("event")
        return _media_url(str(event.get("logo") or "")) if isinstance(event, dict) else ""

    # ── Base embed factory ────────────────────────────────────────────────────

    def _create_base_embed(self, title: str, description: str = "", footer_text: str = "") -> Embed:
        """Crée un embed de base avec le style commun."""
        embed = card("moduleVlrgg", title=title, description=description, timestamp=Timestamp.now())
        if footer_text:
            sign(embed, "moduleVlrgg", footer_text)
        return embed

    # ── Utility formatters ────────────────────────────────────────────────────

    @staticmethod
    def _format_score(score_1: Any, score_2: Any) -> str:
        """Formate un score avec le gagnant en gras (tel quel s'il n'est pas numérique)."""
        try:
            s1, s2 = int(score_1), int(score_2)
        except (ValueError, TypeError):
            return f"{score_1}-{score_2}"
        if s1 > s2:
            return f"**{s1}**-{s2}"
        if s2 > s1:
            return f"{s1}-**{s2}**"
        return f"{s1}-{s2}"
