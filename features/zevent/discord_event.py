"""Plan the Discord scheduled event that mirrors the tracked edition.

Discord's own UI already shows an event's start and end in each viewer's
timezone, so the description carries only what Discord cannot render on its
own: which phase the edition is in and how much has been raised.

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

# The pinned message is the live ticker; the scheduled event only carries a
# headline. Rounding the total down to a step keeps the description stable for
# long stretches — rendering it to the euro would mean one Discord edit per
# refresh cycle for a figure nobody reads that precisely.
DEFAULT_AMOUNT_STEP = 100_000

# Used when the stats API publishes no end for the edition: the marathon runs
# about three days, and Discord requires an end time for an external event.
FALLBACK_DURATION = timedelta(days=3)


@dataclass(frozen=True)
class ScheduledEventPlan:
    """What the guild's scheduled event should look like right now."""

    name: str
    description: str
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


def quantized_total(total: float | None, step: int = DEFAULT_AMOUNT_STEP) -> float | None:
    """Round ``total`` down to ``step``; ``None`` below the first step.

    Below one step there is nothing worth announcing — "plus de 0 €" is noise —
    and a non-positive step disables the line entirely.
    """
    if total is None or step <= 0:
        return None
    floored = (int(total) // step) * step
    return float(floored) if floored > 0 else None


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
    amount_step: int = DEFAULT_AMOUNT_STEP,
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
    amount = quantized_total(total, amount_step)
    if amount is not None:
        lines.append(f"💰 Plus de {format_euros(amount)} récoltés.")
    if tracker_url:
        lines.append(f"📊 [Suivi en direct]({tracker_url})")

    return ScheduledEventPlan(
        name=(title or "Zevent")[:MAX_NAME],
        description="\n\n".join(lines)[:MAX_DESCRIPTION],
        start=event_start,
        end=end,
        status=status,
    )
