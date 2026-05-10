"""EmbedsMixin — embed builders for MDI.

Three variants of the per-match embed (programmé / en direct / terminé) and one
schedule embed listing every Mandatory match.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from interactions import Embed, Timestamp

from features.mdi import GameSnapshot, MatchSnapshot, TeamRef
from src.discord_ext.embeds import format_discord_timestamp

from ._common import (
    EMBED_COLOR_DEFAULT,
    EMBED_COLOR_LIVE,
    EMBED_COLOR_LOSS,
    EMBED_COLOR_SCHEDULED,
    EMBED_COLOR_WIN,
    RAIDERIO_ICON_URL,
    STATUS_EMOJI_LIVE,
    STATUS_EMOJI_SCHEDULED,
    STATUS_EMOJI_TERMINAL_LOSS,
    STATUS_EMOJI_TERMINAL_NEUTRAL,
    STATUS_EMOJI_TERMINAL_WIN,
)

EVENT_TITLE = "MDI Midnight Season 1"


class EmbedsMixin:
    """Mixin: builds the schedule embed and the per-match embed (3 variants)."""

    # ── Author / heading helpers ─────────────────────────────────────────────

    @staticmethod
    def _author_label(phase: str) -> str:
        if phase == "live":
            return f"🔴 LIVE · {EVENT_TITLE}"
        if phase == "terminal":
            return f"🏁 Terminé · {EVENT_TITLE}"
        if phase == "schedule":
            return f"📅 Planning · {EVENT_TITLE}"
        return f"🟡 À venir · {EVENT_TITLE}"

    @classmethod
    def _set_event_author(cls, embed: Embed, event_url: str | None, phase: str) -> None:
        """Attach the Raider.IO branded author block at the top of *embed*."""
        embed.set_author(
            name=cls._author_label(phase),
            url=event_url,
            icon_url=RAIDERIO_ICON_URL,
        )

    def _score_heading(self, match: MatchSnapshot) -> str:
        """Score as an ``##`` heading line for live/terminal descriptions."""
        first = match.first_team
        second = match.second_team
        first_name = first.name if first else "TBD"
        second_name = second.name if second else "TBD"
        first_wins = sum(1 for g in match.games if first and g.winner_team_id == first.id)
        second_wins = sum(1 for g in match.games if second and g.winner_team_id == second.id)
        return f"## **{first_name}**  {first_wins} — {second_wins}  **{second_name}**"

    # ── Match phase classifier ───────────────────────────────────────────────

    @staticmethod
    def _match_phase(match: MatchSnapshot) -> str:
        """Classify a match as ``"scheduled"`` | ``"live"`` | ``"terminal"``.

        Raider.IO flips every game's status from ``"skip"`` to in-progress
        simultaneously when a match begins (not one-by-one as games finish), so
        any non-``"skip"``/``"unstarted"`` game status is the strongest signal
        that the match is live.
        """
        if match.is_terminal:
            return "terminal"
        if match.status and match.status != "unstarted":
            return "live"
        if any(g.status not in ("skip", "unstarted", "") for g in match.games):
            return "live"
        if any(g.winner_team_id is not None for g in match.games):
            return "live"
        return "scheduled"

    # ── Schedule embed ───────────────────────────────────────────────────────

    def _build_schedule_embed(
        self,
        team: TeamRef | None,
        team_slug: str,
        matches: list[MatchSnapshot],
        event_url: str | None = None,
    ) -> Embed:
        """Build the pinned planning embed."""
        team_name = team.name if team else team_slug.capitalize()
        title = f"📅 {team_name} — {EVENT_TITLE}"
        description = f"Suivi automatique des matchs de **{team_name}**."
        embed = Embed(
            title=title,
            description=description,
            color=EMBED_COLOR_DEFAULT,
            timestamp=Timestamp.now(),
        )
        if event_url:
            embed.url = event_url
        if team and team.icon_logo_url:
            embed.set_thumbnail(url=team.icon_logo_url)
        self._set_event_author(embed, event_url, "schedule")

        if not matches:
            embed.add_field(
                name="Aucun match",
                value="Pas de match trouvé pour cette équipe pour l'instant.",
                inline=False,
            )
            return embed

        # Group by bracket while preserving sort order
        groups: dict[str, list[MatchSnapshot]] = {}
        bracket_titles: dict[str, str] = {}
        for match in matches:
            groups.setdefault(match.bracket_slug, []).append(match)
            bracket_titles.setdefault(match.bracket_slug, match.bracket_title)

        for slug, group_matches in groups.items():
            bracket_title = bracket_titles.get(slug, slug)
            blocks: list[str] = [self._schedule_line(match, team) for match in group_matches]
            value = "\n".join(blocks) if blocks else "—"
            # Discord limits each field value to 1024 characters; chunk if needed.
            for chunk_index, chunk in enumerate(self._chunk_field_value(value)):
                name = bracket_title if chunk_index == 0 else f"{bracket_title} (suite)"
                embed.add_field(name=name, value=chunk, inline=False)

        return embed

    def _schedule_line(self, match: MatchSnapshot, team: TeamRef | None) -> str:
        """Two-line block per match: status + matchup, then timestamp as subtext."""
        phase = self._match_phase(match)
        if phase == "terminal":
            if team is not None and match.winner_team_id == team.id:
                emoji = STATUS_EMOJI_TERMINAL_WIN
            elif team is not None and match.winner_team_id is not None:
                emoji = STATUS_EMOJI_TERMINAL_LOSS
            else:
                emoji = STATUS_EMOJI_TERMINAL_NEUTRAL
        elif phase == "live":
            emoji = STATUS_EMOJI_LIVE
        else:
            emoji = STATUS_EMOJI_SCHEDULED

        opponent = match.opponent_of(team.id) if team is not None else None
        opponent_name = opponent.name if opponent else "TBD"
        position = self._position_label(match)

        head_parts: list[str] = [f"{emoji} vs **{opponent_name}**", f"`{position}`"]
        if phase != "scheduled" and team is not None:
            mine = match.games_won_by(team.id)
            theirs = sum(
                1
                for g in match.games
                if g.winner_team_id is not None and g.winner_team_id != team.id
            )
            head_parts.append(f"**{mine} – {theirs}**")
        head = " — ".join(head_parts)

        if match.starts_at is not None:
            sub = (
                f"-# {format_discord_timestamp(match.starts_at, 'F')}"
                f" · {format_discord_timestamp(match.starts_at, 'R')}"
            )
            return f"{head}\n{sub}"
        return head

    @staticmethod
    def _position_label(match: MatchSnapshot) -> str:
        """Short label like ``WB R1`` / ``LB R2`` / ``Tiebreaker``."""
        if match.position == "upper":
            return f"WB R{match.round}"
        if match.position == "lower":
            return f"LB R{match.round}"
        if match.position == "thirdplace":
            return "3e place"
        if match.position:
            return match.position.capitalize()
        return f"Round {match.round}"

    @staticmethod
    def _chunk_field_value(text: str, max_chars: int = 1024) -> list[str]:
        """Split a multi-line value into Discord-field-sized chunks (≤1024)."""
        if len(text) <= max_chars:
            return [text]
        chunks: list[str] = []
        current: list[str] = []
        current_len = 0
        for line in text.splitlines():
            extra = len(line) + (1 if current else 0)
            if current_len + extra > max_chars:
                chunks.append("\n".join(current))
                current = [line]
                current_len = len(line)
            else:
                current.append(line)
                current_len += extra
        if current:
            chunks.append("\n".join(current))
        return chunks

    # ── Match embed (3 variants) ─────────────────────────────────────────────

    def _build_match_embed(
        self, match: MatchSnapshot, team: TeamRef | None, event_url: str | None = None
    ) -> Embed:
        """Build the embed for a single match in its current phase."""
        phase = self._match_phase(match)
        if phase == "terminal":
            return self._build_terminal_embed(match, team, event_url)
        if phase == "live":
            return self._build_live_embed(match, team, event_url)
        return self._build_scheduled_embed(match, team, event_url)

    def _embed_title(self, match: MatchSnapshot, prefix_emoji: str = "") -> str:
        first = match.first_team.name if match.first_team else "TBD"
        second = match.second_team.name if match.second_team else "TBD"
        prefix = f"{prefix_emoji} " if prefix_emoji else ""
        return f"{prefix}{first} vs {second}"

    @staticmethod
    def _bracket_line(match: MatchSnapshot) -> str:
        return f"**{match.bracket_title}** — {EmbedsMixin._position_label(match)} · Match {match.match_order}"

    def _build_scheduled_embed(
        self, match: MatchSnapshot, team: TeamRef | None, event_url: str | None
    ) -> Embed:
        embed = Embed(
            title=self._embed_title(match, STATUS_EMOJI_SCHEDULED),
            color=EMBED_COLOR_SCHEDULED,
            timestamp=Timestamp.now(),
        )
        if event_url:
            embed.url = event_url

        desc_lines = [self._bracket_line(match)]
        if match.starts_at is not None:
            desc_lines.append(f"**{format_discord_timestamp(match.starts_at, 'F')}**")
            desc_lines.append(f"-# Coup d'envoi {format_discord_timestamp(match.starts_at, 'R')}")
        embed.description = "\n".join(desc_lines)

        if match.first_team and match.second_team:
            embed.add_field(name="Affiche", value=self._team_line(match.first_team), inline=True)
            embed.add_field(name="\u200b", value="**vs**", inline=True)
            embed.add_field(name="\u200b", value=self._team_line(match.second_team), inline=True)

        if team and team.icon_logo_url:
            embed.set_thumbnail(url=team.icon_logo_url)
        self._set_event_author(embed, event_url, "scheduled")
        return embed

    def _build_live_embed(
        self, match: MatchSnapshot, team: TeamRef | None, event_url: str | None
    ) -> Embed:
        embed = Embed(
            title=self._embed_title(match, STATUS_EMOJI_LIVE),
            color=EMBED_COLOR_LIVE,
            timestamp=Timestamp.now(),
        )
        if event_url:
            embed.url = event_url

        desc_lines = [self._bracket_line(match), self._score_heading(match)]
        sub_parts = ["En cours"]
        if match.starts_at is not None:
            sub_parts.append(f"début {format_discord_timestamp(match.starts_at, 'R')}")
        watch = self._watch_link(match)
        if watch:
            sub_parts.append(f"📺 {watch}")
        desc_lines.append(f"-# {' · '.join(sub_parts)}")
        embed.description = "\n".join(desc_lines)

        games_text = self._games_block(match)
        if games_text:
            embed.add_field(name="Donjons", value=games_text, inline=False)

        if team and team.icon_logo_url:
            embed.set_thumbnail(url=team.icon_logo_url)
        self._set_event_author(embed, event_url, "live")
        embed.set_footer(text="Mise à jour automatique")
        return embed

    def _build_terminal_embed(
        self, match: MatchSnapshot, team: TeamRef | None, event_url: str | None
    ) -> Embed:
        if team is not None and match.winner_team_id == team.id:
            emoji = STATUS_EMOJI_TERMINAL_WIN
            color = EMBED_COLOR_WIN
            verdict = "VICTOIRE 🎉"
        elif team is not None and match.winner_team_id is not None:
            emoji = STATUS_EMOJI_TERMINAL_LOSS
            color = EMBED_COLOR_LOSS
            verdict = "DÉFAITE 😢"
        else:
            emoji = STATUS_EMOJI_TERMINAL_NEUTRAL
            color = EMBED_COLOR_DEFAULT
            verdict = "Match terminé"

        embed = Embed(
            title=f"{emoji} {self._embed_title(match)}",
            color=color,
            timestamp=Timestamp.now(),
        )
        if event_url:
            embed.url = event_url

        desc_lines = [
            self._bracket_line(match),
            self._score_heading(match),
            f"**{emoji} {verdict}**",
        ]
        watch = self._watch_link(match)
        if watch:
            desc_lines.append(f"-# 📺 Replay : {watch}")
        embed.description = "\n".join(desc_lines)

        games_text = self._games_block(match)
        if games_text:
            embed.add_field(name="Donjons", value=games_text, inline=False)

        if team and team.icon_logo_url:
            embed.set_thumbnail(url=team.icon_logo_url)
        self._set_event_author(embed, event_url, "terminal")
        embed.set_footer(text="Match terminé")
        return embed

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _team_line(team: TeamRef) -> str:
        seed = f"#{team.seed} " if team.seed is not None else ""
        region = f" ({team.region_short})" if team.region_short else ""
        return f"{seed}**{team.name}**{region}"

    def _scoreboard(self, match: MatchSnapshot) -> str:
        first = match.first_team
        second = match.second_team
        first_name = first.name if first else "TBD"
        second_name = second.name if second else "TBD"

        first_wins = sum(1 for g in match.games if first and g.winner_team_id == first.id)
        second_wins = sum(1 for g in match.games if second and g.winner_team_id == second.id)

        first_bold = (
            f"**{first_name}**" if first and match.winner_team_id == first.id else first_name
        )
        second_bold = (
            f"**{second_name}**" if second and match.winner_team_id == second.id else second_name
        )
        first_score = f"**{first_wins}**" if first_wins > second_wins else f"{first_wins}"
        second_score = f"**{second_wins}**" if second_wins > first_wins else f"{second_wins}"
        return f"{first_bold} {first_score} — {second_score} {second_bold}"

    def _games_block(self, match: MatchSnapshot) -> str:
        lines: list[str] = []
        for game in match.games:
            if game.status == "skip":
                continue
            lines.append(self._game_line(game, match))
        return "\n".join(lines)

    @staticmethod
    def _fmt_seconds(total: int | None) -> str:
        """Format seconds as ``M:SS``."""
        if total is None:
            return "—"
        return f"{total // 60}:{total % 60:02d}"

    @staticmethod
    def _fmt_splits(splits: tuple[int, ...]) -> str:
        """Format split times as ``"M:SS·M:SS·…"``."""
        return "·".join(f"{s // 60}:{s % 60:02d}" for s in splits)

    def _team_summary(
        self,
        name: str,
        deaths: int,
        total: int | None,
        winner: bool,
    ) -> str:
        """Line 1 fragment: ``**Name** (0 💀) 12:24``."""
        bold_name = f"**{name}**" if winner else name
        return f"{bold_name} ({deaths} 💀) {self._fmt_seconds(total)}"

    @staticmethod
    def _splits_lines(
        first_splits: tuple[int, ...],
        second_splits: tuple[int, ...],
    ) -> str:
        """One subtext line per split index, fastest time in bold."""
        if not first_splits and not second_splits:
            return ""
        lines: list[str] = []
        for i in range(max(len(first_splits), len(second_splits))):
            t1 = first_splits[i] if i < len(first_splits) else None
            t2 = second_splits[i] if i < len(second_splits) else None
            s1 = f"{t1 // 60}:{t1 % 60:02d}" if t1 is not None else "—"
            s2 = f"{t2 // 60}:{t2 % 60:02d}" if t2 is not None else "—"
            if t1 is not None and t2 is not None:
                if t1 < t2:
                    s1, s2 = f"**{s1}**", s2
                elif t2 < t1:
                    s1, s2 = s1, f"**{s2}**"
            lines.append(f"-# ↳ S{i + 1}  {s1}  ·  {s2}")
        return "\n".join(lines)

    def _game_line(self, game: GameSnapshot, match: MatchSnapshot) -> str:
        dungeon = game.dungeon_name or game.dungeon_short_name or "Donjon TBD"
        level = f" +{game.mythic_level}" if game.mythic_level else ""

        first_name = match.first_team.name if match.first_team else "T1"
        second_name = match.second_team.name if match.second_team else "T2"
        first_id = match.first_team.id if match.first_team else None
        second_id = match.second_team.id if match.second_team else None

        # Per-game phases: Raider.IO progresses sequentially
        # ``unstarted`` → ``in_progress`` → ``complete``. ``skip`` is filtered
        # out upstream. Treat anything else with no winner as in-progress to
        # stay on the safe side.
        if game.winner_team_id is None:
            if game.status == "unstarted":
                header = f"🟡 `G{game.game_order}` **{dungeon}{level}**"
                return f"{header} *(à venir)*"
            header = f"🔴 `G{game.game_order}` **{dungeon}{level}** *en cours*"
            t1 = self._team_summary(
                first_name, game.first_team_deaths, game.first_team_total_seconds, winner=False
            )
            t2 = self._team_summary(
                second_name, game.second_team_deaths, game.second_team_total_seconds, winner=False
            )
            teams_line = f"{t1} vs {t2}"
            splits = self._splits_lines(game.first_team_splits, game.second_team_splits)
            return "\n".join(p for p in (header, teams_line, splits) if p)

        header = f"🏆 `G{game.game_order}` **{dungeon}{level}**"
        first_won = game.winner_team_id == first_id
        second_won = game.winner_team_id == second_id
        t1 = self._team_summary(
            first_name, game.first_team_deaths, game.first_team_total_seconds, winner=first_won
        )
        t2 = self._team_summary(
            second_name, game.second_team_deaths, game.second_team_total_seconds, winner=second_won
        )
        teams_line = f"{t1} vs {t2}"
        splits = self._splits_lines(game.first_team_splits, game.second_team_splits)
        return "\n".join(p for p in (header, teams_line, splits) if p)

    @staticmethod
    def _watch_link(match: MatchSnapshot) -> str:
        for game in match.games:
            if game.video_id and (game.video_type or "").lower() == "youtube":
                return f"[YouTube](https://youtu.be/{game.video_id})"
        return ""

    # ── Misc ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _now_utc() -> datetime:
        return datetime.now(UTC)
