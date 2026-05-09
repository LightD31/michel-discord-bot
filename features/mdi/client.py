"""Raider.IO async client for MDI tournament data.

Source: https://raider.io/api/events/<slug>/brackets[/<bracket_slug>]

This module is pure: it has no Discord dependencies. It returns frozen
dataclasses keyed off the API shape so the extension layer can build embeds
and run diffs without ever touching the raw JSON.

The shared :func:`src.core.http.fetch` is used so we benefit from the global
aiohttp session, retries, and the project's default Mozilla User-Agent (the
default WebFetch UA is rejected by Raider.IO with HTTP 403 — confirmed during
development).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.core import logging as logutil
from src.core.http import fetch

logger = logutil.init_logger(__name__)

EVENT_BASE = "https://raider.io/api/events"

# Cheap in-memory cache to avoid hammering Raider.IO when multiple guilds run
# the same poll cycle within a few seconds of each other. Each entry is keyed
# by absolute URL.
_CACHE_TTL_SECONDS = 30.0
_cache: dict[str, tuple[float, Any]] = {}


def _now_ts() -> float:
    return time.monotonic()


def _parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp from Raider.IO. ``None`` on failure."""
    if not value:
        return None
    try:
        # Raider.IO emits "...Z" as well as "+00:00"; both supported via fromisoformat
        # since Python 3.11.
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except ValueError:
        return None


# ── Domain models ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TeamRef:
    """Lightweight reference to a team as it appears in a bracket match."""

    id: int
    seed: int | None
    name: str
    slug: str
    region_short: str
    region_name: str
    icon_logo_url: str | None
    profile_url: str | None

    @classmethod
    def from_api(cls, data: dict[str, Any] | None) -> TeamRef | None:
        if not data:
            return None
        try:
            team_id = int(data.get("id") or 0)
        except (TypeError, ValueError):
            return None
        if team_id <= 0:
            return None
        region = data.get("region") or {}
        seed = data.get("seed")
        try:
            seed_int = int(seed) if seed is not None else None
        except (TypeError, ValueError):
            seed_int = None
        return cls(
            id=team_id,
            seed=seed_int,
            name=str(data.get("name") or ""),
            slug=str(data.get("slug") or ""),
            region_short=str(region.get("short_name") or ""),
            region_name=str(region.get("name") or ""),
            icon_logo_url=data.get("icon_logo_url") or None,
            profile_url=data.get("teamEventProfileUrl") or None,
        )


def _parse_split_seconds(value: str | None) -> int | None:
    """Parse ``"M:SS"`` split time to total seconds. Returns ``None`` on failure."""
    if not value:
        return None
    parts = value.split(":")
    if len(parts) == 2:
        try:
            return int(parts[0]) * 60 + int(parts[1])
        except ValueError:
            return None
    return None


def _collect_splits(details: dict[str, Any], prefix: str) -> tuple[int, ...]:
    """Return individual split times in seconds; stops at the first null split."""
    splits: list[int] = []
    for i in range(1, 6):
        raw = details.get(f"{prefix}Split{i}")
        if raw is None:
            break
        seconds = _parse_split_seconds(raw)
        if seconds is None:
            break
        splits.append(seconds)
    return tuple(splits)


@dataclass(frozen=True)
class GameSnapshot:
    """One game (dungeon) within a match."""

    id: int
    game_order: int
    status: str
    winner_team_id: int | None
    mythic_level: int | None
    dungeon_name: str | None
    dungeon_short_name: str | None
    keystone_timer_seconds: int | None
    first_team_deaths: int
    second_team_deaths: int
    first_team_splits: tuple[int, ...]
    second_team_splits: tuple[int, ...]

    @property
    def first_team_total_seconds(self) -> int | None:
        return sum(self.first_team_splits) if self.first_team_splits else None

    @property
    def second_team_total_seconds(self) -> int | None:
        return sum(self.second_team_splits) if self.second_team_splits else None

    video_id: str | None
    video_type: str | None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> GameSnapshot:
        details = data.get("details") or {}
        dungeon = data.get("dungeon") or {}
        keystone_ms = dungeon.get("keystone_timer_ms") if isinstance(dungeon, dict) else None
        return cls(
            id=int(data.get("id") or 0),
            game_order=int(data.get("gameOrder") or 0),
            status=str(data.get("status") or "unknown"),
            winner_team_id=_to_int_or_none(data.get("winnerTeamId")),
            mythic_level=_to_int_or_none(details.get("mythicLevel")),
            dungeon_name=(dungeon.get("name") if isinstance(dungeon, dict) else None) or None,
            dungeon_short_name=(dungeon.get("short_name") if isinstance(dungeon, dict) else None)
            or None,
            keystone_timer_seconds=int(keystone_ms) // 1000 if keystone_ms else None,
            first_team_deaths=int(details.get("firstTeamDeaths") or 0),
            second_team_deaths=int(details.get("secondTeamDeaths") or 0),
            first_team_splits=_collect_splits(details, "firstTeam"),
            second_team_splits=_collect_splits(details, "secondTeam"),
            video_id=data.get("videoId") or None,
            video_type=data.get("videoType") or None,
        )


@dataclass(frozen=True)
class MatchSnapshot:
    """A single match between two teams within a bracket."""

    id: int
    bracket_slug: str
    bracket_title: str
    round: int
    match_order: int
    position: str
    status: str
    winner_team_id: int | None
    starts_at: datetime | None
    grace_period_ends_at: datetime | None
    first_team: TeamRef | None
    second_team: TeamRef | None
    winner_team: TeamRef | None
    games: tuple[GameSnapshot, ...]

    def involves_team_id(self, team_id: int) -> bool:
        return (self.first_team is not None and self.first_team.id == team_id) or (
            self.second_team is not None and self.second_team.id == team_id
        )

    def involves_team_slug(self, slug: str) -> bool:
        slug_lower = slug.lower()
        return (self.first_team is not None and self.first_team.slug.lower() == slug_lower) or (
            self.second_team is not None and self.second_team.slug.lower() == slug_lower
        )

    def opponent_of(self, team_id: int) -> TeamRef | None:
        if self.first_team is not None and self.first_team.id == team_id:
            return self.second_team
        if self.second_team is not None and self.second_team.id == team_id:
            return self.first_team
        return None

    @property
    def is_terminal(self) -> bool:
        return self.winner_team_id is not None

    def games_won_by(self, team_id: int) -> int:
        return sum(1 for g in self.games if g.winner_team_id == team_id)


@dataclass(frozen=True)
class BracketInfo:
    """Top-level bracket metadata returned by the brackets list endpoint."""

    id: int
    slug: str
    title: str
    format: str
    starts_at: datetime | None
    ends_at: datetime | None


# ── Helpers ───────────────────────────────────────────────────────────────────


def _to_int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _cached_get(url: str) -> dict[str, Any]:
    """GET *url* with a tiny TTL cache, returning a JSON dict (or {} on error)."""
    cached = _cache.get(url)
    if cached is not None:
        ts, data = cached
        if _now_ts() - ts < _CACHE_TTL_SECONDS:
            return data
    try:
        data = await fetch(url, return_type="json")
    except Exception as e:
        logger.error("Raider.IO fetch failed for %s: %s", url, e)
        # Serve stale rather than nothing if we have it.
        if cached is not None:
            return cached[1]
        return {}
    if not isinstance(data, dict):
        logger.warning("Raider.IO returned non-dict payload for %s", url)
        return {}
    _cache[url] = (_now_ts(), data)
    return data


def invalidate_cache() -> None:
    """Drop the in-memory cache. Call between tests."""
    _cache.clear()


# ── Public API ────────────────────────────────────────────────────────────────


async def list_brackets(event_slug: str) -> list[BracketInfo]:
    """Return the list of brackets for an event."""
    url = f"{EVENT_BASE}/{event_slug}/brackets"
    data = await _cached_get(url)
    out: list[BracketInfo] = []
    for raw in data.get("brackets") or []:
        if not isinstance(raw, dict):
            continue
        try:
            out.append(
                BracketInfo(
                    id=int(raw.get("id") or 0),
                    slug=str(raw.get("slug") or ""),
                    title=str(raw.get("title") or raw.get("slug") or ""),
                    format=str(raw.get("format") or ""),
                    starts_at=_parse_iso(raw.get("bracketStartsDate")),
                    ends_at=_parse_iso(raw.get("bracketEndsDate")),
                )
            )
        except Exception as e:
            logger.warning("Skipping malformed bracket entry: %s", e)
    return out


def _walk_segment_rounds(segment: Any) -> list[dict[str, Any]]:
    """Yield every match dict from a segment's ``rounds`` mapping."""
    if not isinstance(segment, dict):
        return []
    rounds = segment.get("rounds")
    if not isinstance(rounds, dict):
        return []
    matches: list[dict[str, Any]] = []
    for round_matches in rounds.values():
        if isinstance(round_matches, list):
            for m in round_matches:
                if isinstance(m, dict):
                    matches.append(m)
    return matches


def _build_match(
    raw: dict[str, Any], bracket_slug: str, bracket_title: str
) -> MatchSnapshot | None:
    try:
        match_id = int(raw.get("id") or 0)
    except (TypeError, ValueError):
        return None
    if match_id <= 0:
        return None
    games_raw = raw.get("games") or []
    games = tuple(GameSnapshot.from_api(g) for g in games_raw if isinstance(g, dict))
    return MatchSnapshot(
        id=match_id,
        bracket_slug=bracket_slug,
        bracket_title=bracket_title,
        round=int(raw.get("round") or 0),
        match_order=int(raw.get("match") or 0),
        position=str(raw.get("position") or ""),
        status=str(raw.get("status") or "unknown"),
        winner_team_id=_to_int_or_none(raw.get("winnerTeamId")),
        starts_at=_parse_iso(raw.get("startsAt") or raw.get("scheduledAt")),
        grace_period_ends_at=_parse_iso(raw.get("gracePeriodEndsAt")),
        first_team=TeamRef.from_api(raw.get("firstTeam")),
        second_team=TeamRef.from_api(raw.get("secondTeam")),
        winner_team=TeamRef.from_api(raw.get("winnerTeam")),
        games=games,
    )


async def get_bracket_matches(event_slug: str, bracket_slug: str) -> list[MatchSnapshot]:
    """Return every match (upper, lower, tiebreaker, third-place) of a bracket."""
    url = f"{EVENT_BASE}/{event_slug}/brackets/{bracket_slug}"
    data = await _cached_get(url)
    bracket = data.get("bracket")
    if not isinstance(bracket, dict):
        return []
    title = str(bracket.get("title") or bracket_slug)
    matches: list[MatchSnapshot] = []

    segments = bracket.get("segments") or {}
    if isinstance(segments, dict):
        for raw in _walk_segment_rounds(segments.get("upper")):
            m = _build_match(raw, bracket_slug, title)
            if m is not None:
                matches.append(m)
        for raw in _walk_segment_rounds(segments.get("lower")):
            m = _build_match(raw, bracket_slug, title)
            if m is not None:
                matches.append(m)

    for raw in bracket.get("tieBreakerMatches") or []:
        if isinstance(raw, dict):
            m = _build_match(raw, bracket_slug, title)
            if m is not None:
                matches.append(m)

    third = bracket.get("thirdPlaceMatch")
    if isinstance(third, dict):
        m = _build_match(third, bracket_slug, title)
        if m is not None:
            matches.append(m)

    return matches


__all__ = [
    "BracketInfo",
    "GameSnapshot",
    "MatchSnapshot",
    "TeamRef",
    "get_bracket_matches",
    "invalidate_cache",
    "list_brackets",
]
