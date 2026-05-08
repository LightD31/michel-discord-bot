"""ApiMixin — fetches Raider.IO data and resolves Mandatory's matches.

Builds on :mod:`features.mdi.client` (pure data layer) and adds:

- detection of which brackets are currently within their scheduled window,
- resolution of the followed team's id from its slug,
- a stable hash per match used to decide whether the rendered embed needs a
  re-edit, and a stable hash of the schedule list used to decide whether to
  edit the pinned message.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from features.mdi import (
    BracketInfo,
    MatchSnapshot,
    TeamRef,
    get_bracket_matches,
    list_brackets,
)

from ._common import GuildConfig, GuildState, logger


class ApiMixin:
    """Mixin: data fetching, team resolution, and hash helpers."""

    # ── Bracket window logic ─────────────────────────────────────────────────

    @staticmethod
    def _bracket_is_active(bracket: BracketInfo, now: datetime) -> bool:
        """Return True if *now* falls within the bracket's scheduled window."""
        if bracket.starts_at is None or bracket.ends_at is None:
            return False
        return bracket.starts_at <= now <= bracket.ends_at

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(UTC)

    # ── Team resolution + match collection ───────────────────────────────────

    async def _collect_team_matches(
        self, gc: GuildConfig
    ) -> tuple[TeamRef | None, list[MatchSnapshot], list[BracketInfo]]:
        """Return (resolved_team, matches_for_team, all_brackets).

        Walks every bracket of the event and returns matches that include the
        configured team. The team is resolved by slug — Raider.IO exposes the
        slug both on the team object and the bracket payload, so we don't need
        a separate registration lookup.
        """
        brackets: list[BracketInfo] = []
        try:
            brackets = await list_brackets(gc.event_slug)
        except Exception as e:
            logger.error("Failed to list brackets for %s: %s", gc.event_slug, e)
            return None, [], []

        if not brackets:
            return None, [], brackets

        resolved_team: TeamRef | None = None
        team_matches: list[MatchSnapshot] = []
        slug_lower = gc.team_slug.lower()

        for bracket in brackets:
            try:
                matches = await get_bracket_matches(gc.event_slug, bracket.slug)
            except Exception as e:
                logger.warning("Failed to load bracket %s: %s", bracket.slug, e)
                continue

            for match in matches:
                if match.involves_team_slug(slug_lower):
                    team_matches.append(match)
                    if resolved_team is None:
                        if match.first_team and match.first_team.slug.lower() == slug_lower:
                            resolved_team = match.first_team
                        elif match.second_team and match.second_team.slug.lower() == slug_lower:
                            resolved_team = match.second_team

        team_matches.sort(key=self._match_sort_key)
        return resolved_team, team_matches, brackets

    @staticmethod
    def _match_sort_key(match: MatchSnapshot) -> tuple[int, str, int, int]:
        """Sort matches chronologically by bracket then round/match within."""
        # Bracket order matches the natural API order; we approximate it via slug
        # ordering for stability when the API list is missing.
        slug_priority = {
            "group-a": 1,
            "group-b": 2,
            "group-c": 3,
            "season-finals": 4,
            "global-finals": 5,
        }
        priority = slug_priority.get(match.bracket_slug, 99)
        return (priority, match.bracket_slug, match.round, match.match_order)

    # ── Hashing for diff detection ───────────────────────────────────────────

    @staticmethod
    def _match_hash(match: MatchSnapshot) -> str:
        """Stable hash summarising the parts of a match we render."""
        parts: list[str] = [
            match.status,
            str(match.winner_team_id),
            str(match.first_team.id if match.first_team else None),
            str(match.first_team.name if match.first_team else None),
            str(match.second_team.id if match.second_team else None),
            str(match.second_team.name if match.second_team else None),
        ]
        for game in match.games:
            parts.append(
                "|".join(
                    [
                        str(game.id),
                        str(game.game_order),
                        game.status,
                        str(game.winner_team_id),
                        str(game.mythic_level),
                        str(game.dungeon_name),
                        str(game.first_team_deaths),
                        str(game.second_team_deaths),
                        str(game.video_id),
                    ]
                )
            )
        return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()

    @staticmethod
    def _schedule_hash(matches: list[MatchSnapshot]) -> str:
        """Hash of the schedule embed's rendered surface."""
        parts = [str(len(matches))]
        for m in matches:
            parts.append(
                "|".join(
                    [
                        str(m.id),
                        m.bracket_slug,
                        str(m.round),
                        str(m.match_order),
                        m.status,
                        str(m.winner_team_id),
                        str(m.first_team.name if m.first_team else "?"),
                        str(m.second_team.name if m.second_team else "?"),
                        str(sum(1 for g in m.games if g.winner_team_id is not None)),
                    ]
                )
            )
        return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()

    # ── Live-window detection ────────────────────────────────────────────────

    def _has_active_bracket(
        self, brackets: list[BracketInfo], matches: list[MatchSnapshot]
    ) -> bool:
        """True iff at least one Mandatory match's bracket is currently in its window.

        Used by the live-update task to decide whether to do anything at all on
        a given tick; the slow ``schedule`` task runs unconditionally.
        """
        now = self._utc_now()
        active_slugs = {b.slug for b in brackets if self._bracket_is_active(b, now)}
        return any(m.bracket_slug in active_slugs for m in matches)

    # ── State persistence (Mongo) ────────────────────────────────────────────

    async def _load_persisted_matches(self, state: GuildState) -> None:
        """Load match-message metadata from Mongo into ``state.matches``."""
        try:
            docs = await self._matches_col(state.server_id).find({}).to_list(length=None)
        except Exception as e:
            logger.warning("Guild %s: could not load persisted MDI matches: %s", state.server_id, e)
            return
        for doc in docs:
            match_id = doc.get("match_id")
            if isinstance(match_id, int):
                state.matches[match_id] = doc

    async def _persist_match(
        self,
        state: GuildState,
        *,
        match_id: int,
        bracket_slug: str,
        channel_id: str,
        message_id: str,
        last_hash: str,
        terminal: bool,
    ) -> None:
        doc = {
            "_id": f"match:{match_id}",
            "match_id": match_id,
            "bracket_slug": bracket_slug,
            "channel_id": channel_id,
            "message_id": message_id,
            "last_hash": last_hash,
            "terminal": terminal,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        try:
            await self._matches_col(state.server_id).replace_one(
                {"_id": doc["_id"]}, doc, upsert=True
            )
        except Exception as e:
            logger.warning("Guild %s: could not persist match %s: %s", state.server_id, match_id, e)
            return
        state.matches[match_id] = doc

    def _matches_col(self, server_id: str) -> Any:
        from ._common import matches_col

        return matches_col(server_id)
