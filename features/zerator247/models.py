"""Vote cycle of the ZeratoR 24/7 channel and per-member !request reminder settings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

# Phases of ``/api/now``'s ``cycle.phase``: ``idle`` until the requests window
# opens near the end of the VOD, ``requests`` while !request is accepted,
# ``ballot`` while the chat votes, then ``closed``/``due`` once the next VOD is picked.
PHASE_REQUESTS = "requests"
PHASE_BALLOT = "ballot"

# The 24/7 bot picks a random archive VOD itself for this one.
REQUEST_COMMAND = "!request random"

# Two polls of the same cycle can compute an end time a few seconds apart (the
# playhead and the server clock drift); anything closer than this is one cycle.
_SAME_CYCLE_TOLERANCE = timedelta(minutes=2)


@dataclass(frozen=True)
class Cycle:
    """The vote cycle around the end of the VOD airing now."""

    phase: str
    at: datetime
    remaining_seconds: float | None
    request_opens_before: int
    ballot_opens_before: int
    ballot_closes_before: int
    vote_source: str
    stale: bool = False

    @classmethod
    def from_payload(cls, data: Any) -> Cycle | None:
        """Parse ``GET /api/now``; ``None`` when it carries no cycle (e.g. nothing airs)."""
        if not isinstance(data, dict):
            return None
        cycle = data.get("cycle")
        if not isinstance(cycle, dict) or not isinstance(cycle.get("phase"), str):
            return None
        windows = cycle.get("windows") or {}
        remaining = cycle.get("remainingSeconds")
        at = data.get("at")
        try:
            return cls(
                phase=cycle["phase"],
                at=(
                    datetime.fromtimestamp(at / 1000, UTC)
                    if isinstance(at, int | float)
                    else datetime.now(UTC)
                ),
                remaining_seconds=(
                    float(remaining) if isinstance(remaining, int | float) else None
                ),
                request_opens_before=int(windows.get("requestOpensBeforeSeconds", 0)),
                ballot_opens_before=int(windows.get("ballotOpensBeforeSeconds", 0)),
                ballot_closes_before=int(windows.get("ballotClosesBeforeSeconds", 0)),
                vote_source=str(cycle.get("voteSource") or "poll"),
                stale=bool(data.get("stale", False)),
            )
        except (TypeError, ValueError):
            return None

    @property
    def ends_at(self) -> datetime | None:
        """When the VOD airing now ends (the cycle's identity)."""
        if self.remaining_seconds is None:
            return None
        return self.at + timedelta(seconds=self.remaining_seconds)

    def _before_end(self, seconds: int) -> datetime | None:
        ends_at = self.ends_at
        return None if ends_at is None else ends_at - timedelta(seconds=seconds)

    @property
    def requests_open_at(self) -> datetime | None:
        return self._before_end(self.request_opens_before)

    @property
    def ballot_opens_at(self) -> datetime | None:
        """Also when the requests window closes."""
        return self._before_end(self.ballot_opens_before)

    @property
    def ballot_closes_at(self) -> datetime | None:
        return self._before_end(self.ballot_closes_before)


@dataclass(frozen=True)
class RequestSubscription:
    """A member's opt-in to !request reminders, optionally limited to some hours.

    ``start_hour``/``end_hour`` (Paris time) bound the hours reminders are sent:
    from ``start_hour`` included to ``end_hour`` excluded, wrapping past midnight
    when ``start_hour > end_hour``. Either unset, or both equal, means all day.
    """

    user_id: str
    start_hour: int | None = None
    end_hour: int | None = None

    def accepts(self, hour: int) -> bool:
        start, end = self.start_hour, self.end_hour
        if start is None or end is None or start == end:
            return True
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end

    def describe_hours(self) -> str:
        start, end = self.start_hour, self.end_hour
        if start is None or end is None or start == end:
            return "à toute heure"
        return f"entre {start}h et {end}h"


def should_notify(cycle: Cycle | None, last_notified_end: datetime | None) -> bool:
    """True when the requests window is open on a cycle not yet announced."""
    if cycle is None or cycle.stale or cycle.phase != PHASE_REQUESTS:
        return False
    ends_at = cycle.ends_at
    if ends_at is None:
        return False
    return last_notified_end is None or abs(ends_at - last_notified_end) > _SAME_CYCLE_TOLERANCE


def _ts(moment: datetime, style: str) -> str:
    return f"<t:{int(moment.timestamp())}:{style}>"


def build_request_reminder(
    cycle: Cycle,
    *,
    twitch_url: str | None,
    votes_url: str | None,
) -> str:
    """DM sent to subscribers when the requests window opens."""
    where = f"dans le chat de {twitch_url}" if twitch_url else "dans le chat de ZeratoR 24/7"
    message = f"📼 Les requêtes sont ouvertes sur ZeratoR 24/7 ! Tape `{REQUEST_COMMAND}` {where}"
    closes = cycle.ballot_opens_at
    if closes is not None:
        message += f" avant {_ts(closes, 't')} ({_ts(closes, 'R')})"
    message += "."
    if votes_url:
        message += f"\nVOD et vote en cours : {votes_url}"
    return message


def describe_cycle(cycle: Cycle | None) -> str:
    """One-line status of the cycle, for ``/zunivers 247 status``."""
    if cycle is None or cycle.ends_at is None:
        return "Aucune VOD en cours de diffusion sur ZeratoR 24/7."
    if cycle.phase == PHASE_REQUESTS and cycle.ballot_opens_at is not None:
        return f"Les requêtes sont ouvertes, le vote démarre {_ts(cycle.ballot_opens_at, 'R')}."
    if cycle.phase == PHASE_BALLOT and cycle.ballot_closes_at is not None:
        return f"Le vote est en cours, il se termine {_ts(cycle.ballot_closes_at, 'R')}."
    if cycle.phase in ("closed", "due"):
        return "La prochaine VOD est choisie, les requêtes rouvriront à la fin de la suivante."
    if cycle.requests_open_at is not None:
        return f"Les prochaines requêtes ouvrent {_ts(cycle.requests_open_at, 'R')}."
    return "Le vote n'est pas encore ouvert."
