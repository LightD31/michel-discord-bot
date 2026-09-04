"""Plan the Discord scheduled event that mirrors the tracked edition.

Discord's own UI already shows an event's start and end in each viewer's
timezone, so the description carries only what Discord cannot render on its
own: which phase the edition is in and how much has been raised — the total
riding in the name too, so it reads from the server's event list. Editing a
scheduled event notifies nobody, so it is refreshed as it moves. The event's
location follows the edition the same way: the opening concert has a single
Twitch channel to point at, the marathon has the event's own site.

Pure logic — the extension layer turns a :class:`ScheduledEventPlan` into
``interactions`` calls, so the phase rules stay unit-testable without a
gateway connection.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from features.zevent.history import format_euros

SCHEDULED = "scheduled"
"""The edition hasn't opened yet: Discord announces it and takes RSVPs."""
ACTIVE = "active"
"""The edition is under way."""
COMPLETED = "completed"
"""The edition is over — the caller ends (or cancels) the Discord event."""

# Discord's own caps on a guild scheduled event.
MAX_NAME = 100
MAX_DESCRIPTION = 1000

# Name shown when the stats API has not given the edition one yet.
DEFAULT_NAME = "Zevent"
NAME_SEPARATOR = " - "

# Used when the stats API publishes no end for the edition: the marathon runs
# about three days, and Discord requires an end time for an external event.
FALLBACK_DURATION = timedelta(days=3)


@dataclass(frozen=True)
class ScheduledEventPlan:
    """What the guild's scheduled event should look like right now."""

    name: str
    description: str
    location: str
    """Where the event points: see :func:`event_location`."""
    start: datetime
    end: datetime
    status: str
    """One of :data:`SCHEDULED`, :data:`ACTIVE` or :data:`COMPLETED`."""


def resolve_end(
    event_end: datetime | None, event_start: datetime, main_event_start: datetime
) -> datetime:
    """Pick the event's end, falling back on a duration past the last start.

    A published end that lands before the edition even starts is unusable
    (Discord rejects it), so it is treated as missing.
    """
    latest_start = max(event_start, main_event_start)
    if event_end is not None and event_end > latest_start:
        return event_end
    return latest_start + FALLBACK_DURATION


def event_location(
    *, now: datetime, main_event_start: datetime, twitch_url: str, website_url: str
) -> str:
    """Where the Discord event points at this instant.

    The opening concert runs on a single Twitch channel, so that is the place
    to send people. From the marathon on there is no single channel — every
    participant streams on their own — and the event's own site is the hub.

    Either URL alone is enough: whichever is configured stands in for the
    other, so a half-configured module still produces a usable event.
    """
    if now < main_event_start:
        return twitch_url or website_url
    return website_url or twitch_url


def build_name(title: str, total: float | None) -> str:
    """The event's name: the edition, plus the running total once there is one.

    Discord lists events by name, so the figure is legible from the server's
    event list without opening anything. When the 100-character cap bites, the
    edition name gives way rather than the total — the total is the part
    members are watching.
    """
    base = title or DEFAULT_NAME
    if total is None or total <= 0:
        return base[:MAX_NAME]

    suffix = f"{NAME_SEPARATOR}{format_euros(total)}"
    room = MAX_NAME - len(suffix)
    if room <= 0:
        # A total long enough to fill the name on its own is not reachable in
        # euros, but the slice keeps Discord from rejecting the payload.
        return suffix.lstrip()[:MAX_NAME]
    return f"{base[:room].rstrip()}{suffix}"


def amount_line(total: float | None) -> str | None:
    """The donation line, or ``None`` while there is nothing to announce.

    Editing a scheduled event notifies nobody, so the figure tracks the live
    total rather than being rounded to keep the description still.
    """
    if total is None or total <= 0:
        return None
    return f"💰 {format_euros(total)} récoltés."


def _phase_line(
    *, status: str, now: datetime, main_event_start: datetime, concert_active: bool
) -> str:
    if status == COMPLETED:
        return "🏁 L'édition est terminée. Merci à toutes et tous !"
    if status == SCHEDULED:
        return "🎵 Le concert d'ouverture lance l'édition, avant le marathon caritatif."
    if concert_active:
        return "🎵 Concert d'ouverture en direct !"
    if now < main_event_start:
        return "🎵 Concert d'ouverture — le marathon caritatif suit."
    return "🎮 Marathon caritatif en cours."


def plan_scheduled_event(
    *,
    title: str,
    event_start: datetime,
    main_event_start: datetime,
    event_end: datetime | None,
    now: datetime,
    total: float | None = None,
    concert_active: bool = False,
    finished: bool = False,
    tracker_url: str | None = None,
    stats_url: str | None = None,
    twitch_url: str = "",
    website_url: str = "",
) -> ScheduledEventPlan:
    """Describe the scheduled event for the current instant.

    ``finished`` forces the completed status (``/zevent_finish``); otherwise the
    phase follows the clock. The returned ``start`` is the edition's own start:
    a caller creating the event mid-edition has to move it forward itself,
    since Discord refuses a start in the past.
    """
    end = resolve_end(event_end, event_start, main_event_start)

    if finished or now >= end:
        status = COMPLETED
    elif now >= event_start:
        status = ACTIVE
    else:
        status = SCHEDULED

    lines = [
        _phase_line(
            status=status,
            now=now,
            main_event_start=main_event_start,
            concert_active=concert_active,
        )
    ]
    amount = amount_line(total)
    if amount is not None:
        lines.append(amount)
    links = []
    if tracker_url:
        links.append(f"📊 [Suivi en direct]({tracker_url})")
    if stats_url:
        # The planning, the LAN/remote split and the donation goals all come
        # from this community project — crediting it is the least it is owed.
        links.append(f"📈 [Statistiques]({stats_url})")
    if links:
        lines.append(" · ".join(links))

    return ScheduledEventPlan(
        name=build_name(title, total),
        description="\n\n".join(lines)[:MAX_DESCRIPTION],
        location=event_location(
            now=now,
            main_event_start=main_event_start,
            twitch_url=twitch_url,
            website_url=website_url,
        ),
        start=event_start,
        end=end,
        status=status,
    )
