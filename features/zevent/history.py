"""Past editions' donation curves, for comparing this year against last.

The community project publishes a per-edition metrics file on its object
storage — a cumulative donation curve sampled every ten minutes. That is
static once an edition is over, and it is served from a cache bucket rather
than the API server, so reading it costs the project essentially nothing.

Curves are keyed by *elapsed time since the marathon started* rather than by
wall clock: editions begin on different dates, and the question worth asking
is "how far in were we when this happened", not "what was the date".

Pure parsing and arithmetic — the fetch lives in ``extensions/zevent/api.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DonationCurve:
    """A past edition's cumulative donations, in euros, over elapsed seconds."""

    label: str
    points: list[tuple[float, float]]
    """``(seconds since the curve's first sample, euros)``, ascending."""

    @property
    def total(self) -> float:
        return self.points[-1][1] if self.points else 0.0

    @property
    def duration(self) -> float:
        return self.points[-1][0] if self.points else 0.0


def parse_metrics(payload: Any, label: str) -> DonationCurve | None:
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

    pairs: list[tuple[float, float]] = []
    for stamp, value in zip(labels, values, strict=False):
        if isinstance(stamp, int | float) and isinstance(value, int | float):
            pairs.append((float(stamp), float(value)))
    if len(pairs) < 2:
        return None

    # The published order is not guaranteed ascending — the 2024 file ships its
    # viewer labels reversed — so sort before anchoring on the first sample.
    pairs.sort()
    origin = pairs[0][0]
    return DonationCurve(label=label, points=[((t - origin) / 1000, v) for t, v in pairs])


def amount_at(curve: DonationCurve, elapsed: float) -> float | None:
    """Euros raised ``elapsed`` seconds into that edition, interpolated.

    ``None`` before the curve starts; past its end the final total stands —
    the edition was simply over by then.
    """
    if not curve.points or elapsed < 0:
        return None
    if elapsed >= curve.duration:
        return curve.total

    previous = curve.points[0]
    for point in curve.points[1:]:
        if point[0] >= elapsed:
            span = point[0] - previous[0]
            if span <= 0:
                return point[1]
            ratio = (elapsed - previous[0]) / span
            return previous[1] + ratio * (point[1] - previous[1])
        previous = point
    return curve.total


def elapsed_to_reach(curve: DonationCurve, amount: float) -> float | None:
    """Seconds that edition took to reach ``amount``; ``None`` if it never did."""
    for elapsed, value in curve.points:
        if value >= amount:
            return elapsed
    return None


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

    "ZEvent 2025" reads better as "2025" inside "10 min d'avance sur 2025".
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
    curve: DonationCurve | None, milestone: float, elapsed_now: float
) -> str | None:
    """One line placing a milestone against the same point in a past edition.

    ``None`` when there is nothing honest to say — no curve, or the milestone
    was crossed before this year's marathon even opened (during the concert,
    where the two editions are not comparable).
    """
    if curve is None or not curve.points or elapsed_now < 0:
        return None

    reference = elapsed_to_reach(curve, milestone)
    if reference is None:
        return f"🏆 Jamais atteint en {curve.label} (record : {format_euros(curve.total)})"

    delta = reference - elapsed_now
    took = format_duration(reference)
    if abs(delta) < 300:  # under five minutes apart, calling it a lead is noise
        return f"⏱️ Comme en {curve.label}, atteint après {took}"
    if delta > 0:
        return f"⏱️ {format_duration(delta)} d'avance sur {curve.label} (atteint après {took})"
    return f"⏱️ {format_duration(-delta)} de retard sur {curve.label} (atteint après {took})"
