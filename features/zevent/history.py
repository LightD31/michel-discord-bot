"""Past editions' donation curves, for comparing this year against last.

The community project publishes a per-edition metrics file on its object
storage — a cumulative donation curve sampled every ten minutes. That is
static once an edition is over, and it is served from a cache bucket rather
than the API server, so reading it costs the project essentially nothing.

Editions are aligned on **day of the marathon and time of day**, not on raw
elapsed time. Donations follow the clock: evenings peak, nights go quiet. The
honest question is "where were we last year on the second evening at 21:00",
and a weekday reads better than "after 53 hours".

The anchor is each edition's own ``schedule_raising.start``, not its curve's
first sample: these files are rolling windows, not event recordings. The 2025
curve begins eight hours after that edition opened and already sits at
164 452 €, so anything below a curve's floor has no knowable crossing time and
is reported as unknown rather than guessed.

Pure parsing and arithmetic — the fetch lives in ``extensions/zevent/api.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_WEEKDAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")


def _display_zone() -> tzinfo:
    """The audience's clock. Falls back to UTC if the tz database is absent."""
    try:
        return ZoneInfo("Europe/Paris")
    except (ZoneInfoNotFoundError, KeyError):  # pragma: no cover - image-dependent
        return UTC


DISPLAY_TZ = _display_zone()
_MONTHS = (
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


@dataclass(frozen=True)
class DonationCurve:
    """A past edition's cumulative donations, in euros, against wall clock."""

    label: str
    points: list[tuple[datetime, float]]
    """``(timestamp, euros)``, ascending."""
    event_start: datetime | None = None
    """That edition's ``schedule_raising.start`` — the anchor for aligning."""

    @property
    def start(self) -> datetime:
        return self.points[0][0]

    @property
    def anchor(self) -> datetime:
        """What day 0 means for this edition."""
        return self.event_start or self.start

    @property
    def floor(self) -> float:
        """Euros already raised at the curve's first sample.

        The files are rolling windows, so a curve rarely starts at zero: 2025
        opens at 164 452 €. Nothing below this can be dated from it.
        """
        return self.points[0][1] if self.points else 0.0

    @property
    def total(self) -> float:
        return self.points[-1][1] if self.points else 0.0


def parse_metrics(
    payload: Any, label: str, event_start: datetime | None = None
) -> DonationCurve | None:
    """Parse a ``metrics/{event}/global.json`` body into a curve.

    Returns ``None`` for anything unusable, so a malformed or truncated cache
    file simply disables the comparison rather than breaking a notification.

    Note the units: the file's top-level ``donation_amount`` is in centimes,
    but the graph values are already euros. Only the graph is read here.
    """
    if not isinstance(payload, dict):
        return None
    graph = payload.get("graph")
    block = graph.get("donations", {}).get("all", {}) if isinstance(graph, dict) else {}
    if not isinstance(block, dict):
        return None

    labels, values = block.get("labels"), block.get("values")
    if not isinstance(labels, list) or not isinstance(values, list):
        return None

    pairs: list[tuple[datetime, float]] = []
    for stamp, value in zip(labels, values, strict=False):
        if isinstance(stamp, int | float) and isinstance(value, int | float):
            pairs.append((datetime.fromtimestamp(stamp / 1000, UTC), float(value)))
    if len(pairs) < 2:
        return None

    # The published order is not guaranteed ascending — the 2024 file ships its
    # viewer labels reversed — so sort before anything reads the first sample.
    pairs.sort()
    return DonationCurve(label=label, points=pairs, event_start=event_start)


def align(when: datetime, this_start: datetime, reference_start: datetime) -> datetime:
    """Map ``when`` onto the reference edition's calendar.

    Same day of the marathon and same time of day: day 2 at 21:00 this year
    becomes day 2 at 21:00 that year, whatever dates those fell on.

    Days are counted on Paris calendar dates, so both editions are cut at the
    same local midnight. A 00:30 milestone therefore counts as the following
    day on both sides — consistent, even though the audience lived it as the
    tail of the previous evening.
    """
    local = when.astimezone(DISPLAY_TZ)
    day = (local.date() - this_start.astimezone(DISPLAY_TZ).date()).days
    reference_local = reference_start.astimezone(DISPLAY_TZ)
    return datetime.combine(
        reference_local.date() + timedelta(days=day),
        local.timetz(),
    )


def amount_at(curve: DonationCurve, when: datetime) -> float | None:
    """Euros raised by ``when`` in that edition, interpolated between samples.

    ``None`` before the curve starts; past its end the final total stands —
    the edition was simply over by then.
    """
    if not curve.points or when < curve.points[0][0]:
        return None
    if when >= curve.points[-1][0]:
        return curve.total

    previous = curve.points[0]
    for point in curve.points[1:]:
        if point[0] >= when:
            span = (point[0] - previous[0]).total_seconds()
            if span <= 0:
                return point[1]
            ratio = (when - previous[0]).total_seconds() / span
            return previous[1] + ratio * (point[1] - previous[1])
        previous = point
    return curve.total


def reached_at(curve: DonationCurve, amount: float) -> datetime | None:
    """When that edition first reached ``amount``; ``None`` if it never did."""
    for when, value in curve.points:
        if value >= amount:
            return when
    return None


def format_moment(when: datetime) -> str:
    """The audience's view of a moment: ``dimanche à 18 h 10``, Paris time.

    The calendar date is deliberately absent — it belongs to a past edition
    and would only invite the reader to compare the wrong things. What carries
    meaning is which day of the marathon it was, and at what hour.
    """
    local = when.astimezone(DISPLAY_TZ)
    return f"{_WEEKDAYS[local.weekday()]} à {local.hour} h {local.minute:02d}"


def format_duration(seconds: float) -> str:
    """Human duration in French, coarse on purpose: ``2 j 3 h``, ``45 min``."""
    total = max(int(seconds), 0)
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days} j {hours} h" if hours else f"{days} j"
    if hours:
        return f"{hours} h {minutes:02d}" if minutes else f"{hours} h"
    return f"{minutes} min"


def format_euros(amount: float) -> str:
    """Euros with space separators, no decimals."""
    return f"{amount:,.0f} €".replace(",", " ")


def edition_label(name: str) -> str:
    """Short label for an edition: its year when the name carries one.

    "ZEvent 2025" reads better as "2025" inside "4 h d'avance sur 2025".
    """
    tail = name.strip().split()[-1] if name.strip() else ""
    return tail if tail.isdigit() and len(tail) == 4 else name.strip() or "l'édition précédente"


def comparable_editions(events: list[dict], tracked: dict) -> list[dict]:
    """Past editions of the same series as ``tracked``, most recent first.

    The API groups the ZEvent editions under a shared ``event_group_id``;
    one-off fundraisers carry none. Filtering on it keeps the comparison
    between like and like instead of measuring a marathon against a weekend
    charity stream.
    """
    group = tracked.get("event_group_id")
    if not group:
        return []
    start = _start_of(tracked)
    if start is None:
        return []

    peers: list[tuple[str, dict]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("event_group_id") != group or event.get("id") == tracked.get("id"):
            continue
        # An edition with no usable start cannot be placed before or after the
        # tracked one, so it is simply not a candidate.
        peer_start = _start_of(event)
        if peer_start is None or peer_start >= start:
            continue
        peers.append((peer_start, event))

    return [event for _, event in sorted(peers, key=lambda pair: pair[0], reverse=True)]


def _start_of(event: dict) -> str | None:
    schedule = event.get("schedule")
    start = schedule.get("start") if isinstance(schedule, dict) else None
    return start if isinstance(start, str) else None


def compare_milestone(
    curve: DonationCurve | None,
    milestone: float,
    now: datetime,
    this_start: datetime,
) -> str | None:
    """One line placing a milestone against the same point of a past edition.

    ``None`` when there is nothing honest to say — no curve, or the milestone
    landed before this year's marathon opened (during the concert, where the
    two editions are not comparable).
    """
    if curve is None or not curve.points:
        return None

    # Before the marathon opens there is nothing to compare against: the
    # published curves are rolling windows that do not reach back to their own
    # pre-event concert, so a Thursday milestone has no counterpart.
    if now < this_start:
        return None

    if milestone < curve.floor:
        # That edition was already past this figure when its recording began,
        # so when it crossed is unknowable — better silent than invented.
        return None

    when = reached_at(curve, milestone)
    if when is None:
        return f"🏆 Jamais atteint en {curve.label} (record : {format_euros(curve.total)})"

    # Where this moment falls on that edition's calendar — same day of the
    # marathon, same time of day — is what makes the two comparable.
    equivalent = align(now, this_start, curve.anchor)
    delta = (when - equivalent).total_seconds()
    moment = format_moment(when)

    if abs(delta) < 300:  # under five minutes apart, calling it a lead is noise
        return f"⏱️ Comme en {curve.label}, atteint {moment}"
    if delta > 0:
        return f"⏱️ {format_duration(delta)} d'avance sur {curve.label} (atteint {moment})"
    return f"⏱️ {format_duration(-delta)} de retard sur {curve.label} (atteint {moment})"
