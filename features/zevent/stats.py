"""Parsers for the EvenMoreStats event API used by the Zevent tracker.

The 2026 edition moved the planning and the LAN/remote split off ``zevent.fr``:

- ``zevent.fr/api/`` still serves donation totals, viewer counts and the
  per-streamer list, but its entries no longer carry a ``location`` field.
- The former planning host (``zevent-api.gdoc.fr``) is gone (404). Planning
  and participant metadata now come from the EvenMoreStats API:
  ``/events``, ``/events/{id}/donation_goals/overview`` and
  ``/events/{id}/shows``.

Pure parsing only — the HTTP calls live in ``extensions/zevent/api.py`` so this
module stays importable (and testable) without the ``aiohttp`` chain.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from features.zevent.models import DonationGoal, Participant, Show

# Only the on-site venue counts as "LAN". The satellite locations the API
# reports (``remote_zbase``, ``remote_villa``, ``remote_ankama``, …) are
# remote setups and belong with the online participants.
LAN_LOCATIONS = frozenset({"lan", "on_site", "onsite"})

LAN = "LAN"
ONLINE = "Online"


def location_bucket(raw: str | None) -> str:
    """Map a stats-API ``location`` onto the bucket the embeds render."""
    if not raw:
        return ONLINE
    return LAN if raw.strip().lower() in LAN_LOCATIONS else ONLINE


def parse_datetime(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp, tolerating a trailing ``Z`` and ``None``."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _twitch_socials(entry: dict) -> tuple[str, str]:
    """Return ``(login, id)`` from an entry's ``socials.twitch`` block."""
    socials = entry.get("socials")
    twitch = socials.get("twitch") if isinstance(socials, dict) else None
    if not isinstance(twitch, dict):
        return "", ""
    login = str(twitch.get("login") or "").lower()
    twitch_id = str(twitch.get("id") or "")
    return login, twitch_id


def _parse_goal(entry: dict) -> DonationGoal | None:
    """Parse ``next_donation_goal``; ``None`` when absent or unusable."""
    goal = entry.get("next_donation_goal")
    if not isinstance(goal, dict):
        return None
    amount = goal.get("amount")
    if not isinstance(amount, int | float):
        return None
    name = str(goal.get("name") or "").strip()
    if not name:
        return None
    return DonationGoal(name=name, amount=float(amount) / 100)


def parse_participants(payload: Any) -> list[Participant]:
    """Parse the ``donation_goals/overview`` payload into :class:`Participant`.

    Entries without a usable Twitch login are dropped: the tracker matches them
    against the ``zevent.fr`` streamer list by login, so an entry we cannot key
    is of no use downstream.
    """
    if not isinstance(payload, list):
        return []

    participants: list[Participant] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        login, twitch_id = _twitch_socials(entry)
        if not login:
            continue
        raw_location = str(entry.get("location") or "")
        amount = entry.get("amount_raised")
        participants.append(
            Participant(
                display_name=str(entry.get("name") or login),
                twitch_login=login,
                twitch_id=twitch_id,
                location=location_bucket(raw_location),
                raw_location=raw_location,
                live=bool(entry.get("live")),
                amount_raised=(float(amount) / 100) if isinstance(amount, int | float) else 0.0,
                next_goal=_parse_goal(entry),
            )
        )
    return participants


def build_location_index(participants: list[Participant]) -> dict[str, str]:
    """Index participants by Twitch login *and* Twitch id onto their bucket.

    Keying on both means a ``zevent.fr`` entry still resolves when one of the
    two identifiers is missing or the login was changed on one side only.
    """
    index: dict[str, str] = {}
    for participant in participants:
        if participant.twitch_login:
            index[participant.twitch_login] = participant.location
        if participant.twitch_id:
            index[participant.twitch_id] = participant.location
    return index


def resolve_location(stream: dict, index: dict[str, str]) -> str:
    """Bucket one ``zevent.fr`` live entry, preferring its own ``location``.

    ``zevent.fr`` dropped the field in 2026 but may bring it back; when it is
    present it wins, otherwise the stats-API index decides, and an unknown
    streamer falls back to ``Online`` (the far larger group).
    """
    own = stream.get("location")
    if isinstance(own, str) and own:
        return location_bucket(own) if own not in (LAN, ONLINE) else own

    login = str(stream.get("twitch") or "").lower()
    if login and login in index:
        return index[login]
    twitch_id = str(stream.get("twitch_id") or "")
    if twitch_id and twitch_id in index:
        return index[twitch_id]
    return ONLINE


def parse_shows(payload: Any) -> list[Show]:
    """Parse the ``shows`` payload into :class:`Show` objects, sorted by start."""
    if not isinstance(payload, list):
        return []

    shows: list[Show] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        schedule = entry.get("schedule")
        schedule = schedule if isinstance(schedule, dict) else {}

        hosts: list[str] = []
        guests: list[str] = []
        for participant in entry.get("participants") or []:
            if not isinstance(participant, dict):
                continue
            name = str(participant.get("streamer_name") or "").strip()
            if not name:
                continue
            if str(participant.get("role") or "").lower() == "host":
                hosts.append(name)
            else:
                guests.append(name)

        shows.append(
            Show(
                name=str(entry.get("name") or "Événement"),
                description=str(entry.get("description") or ""),
                start=parse_datetime(schedule.get("start")),
                end=parse_datetime(schedule.get("end")),
                all_day=bool(entry.get("all_day")),
                hosts=hosts,
                guests=guests,
            )
        )

    return sorted(
        shows, key=lambda s: (s.start is None, s.start or datetime.min.replace(tzinfo=UTC))
    )


def upcoming_shows(shows: list[Show], now: datetime, limit: int | None = None) -> list[Show]:
    """Shows that have not finished yet, oldest first, optionally capped."""
    pending = [s for s in shows if s.end is None or s.end > now]
    return pending[:limit] if limit is not None else pending


DEFAULT_PROGRESS_WEIGHT = 1.0
DEFAULT_OFFLINE_FACTOR = 0.5
DEFAULT_VELOCITY_WEIGHT = 2.0
# A goal this many minutes out counts as half as urgent as one landing now.
VELOCITY_HORIZON_MINUTES = 10.0


def goal_progress(participant: Participant) -> float:
    """How far along ``participant`` is toward its next goal, in ``[0, 1]``."""
    goal = participant.next_goal
    if goal is None or goal.amount <= 0:
        return 0.0
    return min(participant.amount_raised / goal.amount, 1.0)


def is_live(participant: Participant, live_logins: set[str] | None = None) -> bool:
    """Whether ``participant`` is streaming, preferring a fresh Twitch set.

    ``live_logins`` comes from the Twitch poll the refresh loop already runs
    every cycle. The stats API also reports a ``live`` flag, but it is cached
    for minutes at a time, so it only stands in when Twitch is unavailable.
    """
    if live_logins is None:
        return participant.live
    return participant.twitch_login in live_logins


def velocity_bonus(
    eta_minutes: float | None,
    velocity_weight: float = DEFAULT_VELOCITY_WEIGHT,
    horizon: float = VELOCITY_HORIZON_MINUTES,
) -> float:
    """Additive lift for a goal whose remaining amount is being eaten through.

    ``weight / (1 + eta / horizon)`` — full weight for a goal landing now,
    half of it at the horizon, tapering to nothing for one hours away. A goal
    going nowhere (``eta_minutes`` is ``None``) gets exactly zero.

    Additive on purpose. The base score is logarithmic in euros, so a
    *multiplicative* boost would scale with how big the streamer already is:
    it lifted the leaders by whole points while moving a mid-tier channel by a
    fraction of one, which is the opposite of surfacing a raid. Adding instead
    makes the lift mean something absolute — at the default weight, a goal
    about to fall counts for as much as having raised a hundred times more.
    """
    if eta_minutes is None:
        return 0.0
    weight = max(velocity_weight, 0.0)
    span = max(horizon, 1e-9)
    return weight / (1.0 + max(eta_minutes, 0.0) / span)


def goal_remaining(participant: Participant) -> float:
    """Euros still needed for ``participant``'s next goal."""
    goal = participant.next_goal
    if goal is None:
        return 0.0
    return max(goal.amount - participant.amount_raised, 0.0)


def goal_score(
    participant: Participant,
    progress_weight: float = DEFAULT_PROGRESS_WEIGHT,
    offline_factor: float = DEFAULT_OFFLINE_FACTOR,
    live_logins: set[str] | None = None,
    etas: dict[str, float] | None = None,
    velocity_weight: float = DEFAULT_VELOCITY_WEIGHT,
) -> float:
    """Rank a pending goal by imminence, streamer size, and whether it's watchable.

    ``progress ** weight * log10(1 + raised) * (live ? 1 : offline_factor)``.

    The log matters: donation totals span roughly six orders of magnitude
    (1 € to over 1 M €) while progress spans one, so multiplying the raw
    amount would let money drown out imminence entirely and surface the same
    few names all event.

    ``progress_weight`` slides between the first two concerns — ``0`` ignores
    progress and ranks purely by amount raised, ``1`` balances them, and
    higher values increasingly favour goals about to be reached.

    ``offline_factor`` demotes goals nobody can currently watch fall, without
    letting presence override everything: an earlier version sorted live
    streamers strictly first, which put a live channel at 0.6% of its goal
    above an offline one at 63%. ``1`` ignores the stream status entirely,
    ``0`` pushes every offline streamer behind the live ones.
    """
    weight = max(progress_weight, 0.0)
    progress = goal_progress(participant)
    # 0 ** 0 is 1 in Python, which is what we want at weight 0: the progress
    # term drops out and the ranking becomes pure prominence.
    base = (progress**weight) * math.log10(1 + max(participant.amount_raised, 0.0))
    live = is_live(participant, live_logins)
    base *= 1.0 if live else min(max(offline_factor, 0.0), 1.0)
    eta = (etas or {}).get(participant.twitch_login)
    return base + velocity_bonus(eta, velocity_weight)


def upcoming_goals(
    participants: list[Participant],
    limit: int | None = None,
    progress_weight: float = DEFAULT_PROGRESS_WEIGHT,
    offline_factor: float = DEFAULT_OFFLINE_FACTOR,
    live_logins: set[str] | None = None,
    etas: dict[str, float] | None = None,
    velocity_weight: float = DEFAULT_VELOCITY_WEIGHT,
) -> list[Participant]:
    """Participants with a pending goal, most worth watching first.

    Ranking an unstarted event is degenerate — every amount is still zero, so
    every score is zero — and the goal's own size then decides, which surfaces
    the headline pledges rather than whichever joke opener happens to be
    cheapest. Name breaks the remaining ties so the rendered embed stays
    stable across refreshes instead of churning with API order.
    """
    with_goals = [p for p in participants if p.next_goal is not None]
    ranked = sorted(
        with_goals,
        key=lambda p: (
            -goal_score(p, progress_weight, offline_factor, live_logins, etas, velocity_weight),
            -(p.next_goal.amount if p.next_goal else 0.0),
            p.display_name.lower(),
        ),
    )
    return ranked[:limit] if limit is not None else ranked


def select_event(events: Any, now: datetime, event_id: str | None = None) -> dict | None:
    """Pick the event to track out of the API's ``/events`` listing.

    An explicit ``event_id`` always wins. Otherwise: the edition currently
    running, else the next one to start, else the most recent past one — so the
    tracker follows the new edition as soon as the API publishes it, without a
    config change every year.
    """
    if not isinstance(events, list):
        return None
    candidates = [e for e in events if isinstance(e, dict)]
    if not candidates:
        return None

    if event_id:
        for event in candidates:
            if str(event.get("id") or "") == event_id:
                return event
        return None

    def window(event: dict) -> tuple[datetime | None, datetime | None]:
        schedule = event.get("schedule")
        schedule = schedule if isinstance(schedule, dict) else {}
        return parse_datetime(schedule.get("start")), parse_datetime(schedule.get("end"))

    running = [e for e in candidates if _contains(window(e), now)]
    if running:
        return min(running, key=lambda e: window(e)[0] or datetime.max.replace(tzinfo=UTC))

    upcoming = [e for e in candidates if (window(e)[0] or datetime.min.replace(tzinfo=UTC)) > now]
    if upcoming:
        return min(upcoming, key=lambda e: window(e)[0] or datetime.max.replace(tzinfo=UTC))

    dated = [e for e in candidates if window(e)[0] is not None]
    if dated:
        return max(dated, key=lambda e: window(e)[0] or datetime.min.replace(tzinfo=UTC))
    return None


def event_schedule(event: dict | None) -> tuple[datetime | None, datetime | None]:
    """Return ``(event start, fundraising start)`` for a stats-API event.

    ``schedule.start`` is when the event opens (the pre-event concert in 2026)
    and ``schedule_raising.start`` when donations open (the marathon proper) —
    exactly the two instants the tracker counts down to, so they need not be
    configured by hand each year.
    """
    if not isinstance(event, dict):
        return None, None

    def _start(key: str) -> datetime | None:
        block = event.get(key)
        return parse_datetime(block.get("start")) if isinstance(block, dict) else None

    return _start("schedule"), _start("schedule_raising")


def _contains(bounds: tuple[datetime | None, datetime | None], now: datetime) -> bool:
    start, end = bounds
    if start is None or end is None:
        return False
    return start <= now <= end
