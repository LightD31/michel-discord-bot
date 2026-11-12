"""Past editions' donation curves, for comparing this year against last.

The community project publishes a per-edition metrics file on its object
storage — a cumulative donation curve sampled every ten minutes. That is
static once an edition is over, and it is served from a cache bucket rather
than the API server, so reading it costs the project essentially nothing.

Editions are aligned on **day of the marathon and time of day**, not on raw
elapsed time. Donations follow the clock: evenings peak, nights go quiet. The
honest question is "where were we last year on the second evening at 21:00",
and a weekday reads better than "after 53 hours".

Coverage varies by edition, so nothing may be assumed about it. The 2024 file
is an exact recording — it starts and ends on that edition's fundraising
window to the minute, and its final value matches the API total. The 2025 file
is truncated at both ends: it opens eight hours late at 164 452 € already
raised and stops nearly three hours early, 480 000 € short of the API total.
Anything below a curve's floor therefore has no knowable crossing time and is
reported as unknown rather than guessed.

Pure parsing and arithmetic — the fetch lives in ``extensions/zevent/api.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from src.core.clock import LOCAL_TZ

_WEEKDAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")


# The audience's clock (src/core/clock.py).
DISPLAY_TZ = LOCAL_TZ
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
    final_total: float | None = None
    """That edition's final total in euros, as reported by the API listing.

    Supplied by the caller from ``amount_raised``, which is the only figure
    that knows what an edition actually raised. The metrics file cannot: its
    top-level ``donation_amount`` is the *recording's* total, and a truncated
    recording understates the edition by however much it missed — 2025 files
    16 182 382 € against the 16 658 660 € it really raised.
    """

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

        Not always zero: the 2024 file starts at 8 174 € and the 2025 one at
        164 452 €, having missed that edition's first eight hours. Nothing
        below this figure can be dated from the curve.
        """
        return self.points[0][1] if self.points else 0.0

    @property
    def total(self) -> float:
        return self.points[-1][1] if self.points else 0.0

    @property
    def record(self) -> float:
        """What that edition finished on, best known — never below its curve.

        The API's total when the caller supplied one; otherwise the file's own,
        which is only ever the recording's. Both are floored at the curve's
        last sample, which is standing proof the edition got at least that far.
        """
        return max(self.final_total or 0.0, self.total)


def parse_metrics(
    payload: Any,
    label: str,
    event_start: datetime | None = None,
    final_total: float | None = None,
) -> DonationCurve | None:
    """Parse a ``metrics/{event}/global.json`` body into a curve.

    Returns ``None`` for anything unusable, so a malformed or truncated cache
    file simply disables the comparison rather than breaking a notification.

    ``final_total`` is that edition's real total in euros, which only the API
    listing knows (``amount_raised``); pass it whenever it is available.

    Note the units: the file's top-level ``donation_amount`` is in centimes,
    while the graph values are already euros. That top-level figure is only
    the recording's own total — for 2025 it sits 4 k€ above a curve that
    stops 476 k€ short of the edition — so it stands in for ``final_total``
    only when the caller has nothing better.
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
    if final_total is None:
        published = payload.get("donation_amount")
        if isinstance(published, int | float):
            final_total = float(published) / 100
    return DonationCurve(
        label=label,
        points=pairs,
        event_start=event_start,
        final_total=final_total,
    )


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


def reached_at(curve: DonationCurve, amount: float) -> datetime | None:
    """When that edition reached ``amount``; ``None`` if it never did.

    Interpolated linearly within the bracketing pair rather than rounded up to
    the next sample. The curves are sampled every ten minutes in 2025 and every
    thirty in 2024, so returning the sample itself would quantise every
    comparison to that step — enough to swamp the small leads this is meant to
    report. A donation total only ever climbs, so a straight line across one
    short gap cannot invert the ordering.
    """
    if not curve.points:
        return None

    first_when, first_value = curve.points[0]
    if first_value >= amount:
        # Already past it when recording began: the true crossing is at or
        # before this sample, so this is the earliest moment we can name.
        return first_when

    previous = curve.points[0]
    for when, value in curve.points[1:]:
        if value >= amount:
            climbed = value - previous[1]
            if climbed <= 0:  # flat segment; nothing to interpolate across
                return when
            ratio = (amount - previous[1]) / climbed
            return previous[0] + (when - previous[0]) * ratio
        previous = (when, value)
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


def event_total(event: dict) -> float | None:
    """That edition's final total in euros, from the listing's ``amount_raised``.

    In centimes on the wire, like every other amount the API serves.
    """
    amount = event.get("amount_raised") if isinstance(event, dict) else None
    return float(amount) / 100 if isinstance(amount, int | float) else None


def previous_record(events: list[dict], tracked: dict) -> tuple[str, float] | None:
    """The best any past edition of the series managed: ``(label, euros)``.

    The *highest* total, not the most recent one. They are not the same thing
    and the series proves it: ZEvent 2024 raised 10 145 881 €, below the
    10 182 126 € of 2022, so a tracker measuring itself against last year
    would have called the record broken 36 245 € early.

    Read from the ``/events`` listing the tracker already holds, so this costs
    no request and needs no metrics file — an edition whose curve was never
    published still counts toward the record it set.
    """
    best: tuple[str, float] | None = None
    for event in comparable_editions(events, tracked):
        total = event_total(event)
        if total is None or total <= 0:
            continue
        if best is None or total > best[1]:
            best = (edition_label(str(event.get("name") or "")), total)
    return best


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

    # Before the marathon opens there is nothing to compare against: no
    # published curve reaches back to its own pre-event concert, so a Thursday
    # milestone has no counterpart.
    if now < this_start:
        return None

    if milestone < curve.floor:
        # That edition was already past this figure when its recording began,
        # so when it crossed is unknowable — better silent than invented.
        return None

    when = reached_at(curve, milestone)
    if when is None:
        if milestone <= curve.record:
            # That edition did pass this figure — its published total says so —
            # but the recording stops short of it, so when it crossed is
            # unknowable. State the fact without inventing an hour.
            return f"📈 Dépassé en {curve.label} (total final : {format_euros(curve.record)})"
        return f"🏆 Jamais atteint en {curve.label} (record : {format_euros(curve.record)})"

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


def record_message(
    label: str,
    record: float,
    total: float,
    now: datetime,
    main_start: datetime,
) -> str:
    """The announcement for this edition passing the series' best total.

    Takes the record and whose it was rather than a curve: the record comes
    from the API listing, which covers every edition, while a curve exists
    only for the few whose metrics file was published.

    The headline is the record falling; the second line says how far into the
    marathon it fell, which is the part that dates the feat. That duration is
    omitted before the marathon opens rather than rendered negative — remote
    streamers now go live during the pre-event concert, so a crossing then is
    unlikely but not impossible.
    """
    lines = [
        f"🏆 **Record battu !** Le total dépasse les {format_euros(record)} "
        f"de {label} — {format_euros(total)} récoltés ! 🏆"
    ]
    elapsed = (now - main_start).total_seconds()
    if elapsed > 0:
        lines.append(f"⏱️ Il aura fallu {format_duration(elapsed)} de marathon.")
    return "\n".join(lines)
