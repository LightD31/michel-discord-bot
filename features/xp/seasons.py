"""XP seasons (P5): the leaderboard of the month or the quarter.

A season is ``months`` calendar months long, counted from January (1: every
month, 3: quarters, 12: the year), on the community's wall clock. A season's
ranking is summed from ``xp_events``, which are timestamped and kept 400
days, so nothing new is counted; when a season ends its top 10 is kept in
``xp_seasons`` (:meth:`features.xp.XpRepository.record_season`), the podium is posted, and the optional
champion role moves to the winner.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from src.core import clock

MONTH_NAMES = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)
MIN_MONTHS, MAX_MONTHS = 1, 12
PODIUM = ("🥇", "🥈", "🥉")
KEPT_PER_SEASON = 10


@dataclass(frozen=True)
class Season:
    """One season: its key (``2026-11``, ``2026-Q4``, ``2026-11+2``), bounds and name."""

    key: str
    start: datetime  # UTC, included
    end: datetime  # UTC, excluded
    label: str


def clamp_months(value: object) -> int:
    """A season length in months as configured, kept within 1–12 (1 when unreadable)."""
    try:
        months = int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return MIN_MONTHS
    return max(MIN_MONTHS, min(months, MAX_MONTHS))


def _label(year: int, first: int, months: int) -> str:
    last = min(first + months - 1, 12)
    if first == last:
        return f"{MONTH_NAMES[first - 1]} {year}"
    if months == 3 and (first - 1) % 3 == 0:
        return f"T{(first - 1) // 3 + 1} {year}"
    if first == 1 and last == 12:
        return f"l'année {year}"
    return f"{MONTH_NAMES[first - 1]} – {MONTH_NAMES[last - 1]} {year}"


def _key(year: int, first: int, months: int) -> str:
    if months == 1:
        return f"{year}-{first:02d}"
    if months == 3 and (first - 1) % 3 == 0:
        return f"{year}-Q{(first - 1) // 3 + 1}"
    return f"{year}-{first:02d}+{months}"


def season_of(moment: datetime, months: int) -> Season:
    """The season *moment* falls in (a last season of the year may be shorter)."""
    months = clamp_months(months)
    local = clock.to_local(moment)
    first = ((local.month - 1) // months) * months + 1
    last = min(first + months - 1, 12)
    start = clock.at_local(date(local.year, first, 1))
    end = (
        clock.at_local(date(local.year + 1, 1, 1))
        if last == 12
        else clock.at_local(date(local.year, last + 1, 1))
    )
    return Season(_key(local.year, first, months), start, end, _label(local.year, first, months))


def previous_season(moment: datetime, months: int) -> Season:
    """The season just before the one *moment* falls in."""
    current = season_of(moment, months)
    return season_of(current.start - timedelta(seconds=1), months)


def thousands(value: int) -> str:
    """``12 345``: thousands spaced the French way (a narrow no-break space)."""
    return f"{value:,}".replace(",", "\u202f")


def podium_lines(ranking: list[tuple[str, int]], names: dict[str, str]) -> list[str]:
    """``🥇 Camille — 1 234 XP`` for the first three (names fall back to a mention)."""
    lines = []
    for medal, (user_id, xp) in zip(PODIUM, ranking, strict=False):
        lines.append(f"{medal} {names.get(user_id, f'<@{user_id}>')} — {thousands(xp)} XP")
    return lines


__all__ = [
    "KEPT_PER_SEASON",
    "MAX_MONTHS",
    "MIN_MONTHS",
    "Season",
    "clamp_months",
    "podium_lines",
    "thousands",
    "previous_season",
    "season_of",
]
