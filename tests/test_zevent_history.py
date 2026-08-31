"""Unit tests for ``features.zevent.history``.

Shapes come from the community project's metrics cache
(``metrics/{event}/global.json``): a cumulative donation curve sampled every
ten minutes, in euros, while the file's top-level total is in centimes.

Editions are compared on **day of the event and time of day**, not elapsed
seconds — donations follow the clock, and an edition whose recording started
at a different hour must not drag every comparison out of step.
"""

from datetime import UTC, datetime

from features.zevent.history import (
    align,
    amount_at,
    comparable_editions,
    compare_milestone,
    edition_label,
    format_duration,
    format_euros,
    format_moment,
    parse_metrics,
    reached_at,
)

HOUR_MS = 3_600_000
# 2025-09-05 16:00 UTC — a Friday, when the real 2025 curve begins.
REF_ORIGIN = int(datetime(2025, 9, 5, 16, 0, tzinfo=UTC).timestamp() * 1000)
# 2026-09-04 16:00 UTC — a Friday, the real 2026 marathon start.
THIS_START = datetime(2026, 9, 4, 16, 0, tzinfo=UTC)


def _payload(values: list[float], *, origin: int = REF_ORIGIN, step: int = HOUR_MS) -> dict:
    return {
        "donation_amount": int(values[-1] * 100),  # centimes, unlike the graph
        "viewers_max": 752_185,
        "graph": {
            "viewers": {"labels": [], "values": []},
            "donations": {
                "all": {
                    "labels": [origin + i * step for i in range(len(values))],
                    "values": values,
                }
            },
        },
    }


CURVE = parse_metrics(_payload([0, 1_000_000, 4_000_000, 9_000_000, 16_000_000]), "2025")
assert CURVE is not None


# ── parsing ──────────────────────────────────────────────────────────


def test_parses_into_timestamps_and_euros() -> None:
    assert CURVE.label == "2025"
    assert CURVE.start == datetime(2025, 9, 5, 16, 0, tzinfo=UTC)
    assert CURVE.points[1] == (datetime(2025, 9, 5, 17, 0, tzinfo=UTC), 1_000_000.0)
    assert CURVE.total == 16_000_000.0


def test_unsorted_labels_are_ordered_before_anything_reads_the_start() -> None:
    """The 2024 file ships its labels reversed — trusting the order would break."""
    payload = _payload([0, 1_000_000, 4_000_000])
    block = payload["graph"]["donations"]["all"]
    block["labels"].reverse()
    block["values"].reverse()

    curve = parse_metrics(payload, "2024")
    assert curve is not None
    assert curve.start == datetime(2025, 9, 5, 16, 0, tzinfo=UTC)
    assert curve.total == 4_000_000.0


def test_unusable_payloads_disable_the_comparison_rather_than_raising() -> None:
    assert parse_metrics(None, "x") is None
    assert parse_metrics({}, "x") is None
    assert parse_metrics({"graph": "nope"}, "x") is None
    assert parse_metrics({"graph": {"donations": {"all": {}}}}, "x") is None
    assert parse_metrics(_payload([1_000_000]), "x") is None  # one point is not a curve

    payload = _payload([0, 1_000_000, 4_000_000])
    payload["graph"]["donations"]["all"]["values"] = [0, "beaucoup", 4_000_000]
    curve = parse_metrics(payload, "x")
    assert curve is not None
    assert len(curve.points) == 2


# ── calendar alignment ───────────────────────────────────────────────


def test_align_maps_the_same_event_day_and_time_of_day() -> None:
    # Day 0 at 20:00 in 2026 is day 0 at 20:00 in 2025 — Friday to Friday.
    assert align(datetime(2026, 9, 4, 20, 0, tzinfo=UTC), THIS_START, CURVE.start) == datetime(
        2025, 9, 5, 20, 0, tzinfo=UTC
    )
    # Day 2 at 18:30 — Sunday to Sunday.
    assert align(datetime(2026, 9, 6, 18, 30, tzinfo=UTC), THIS_START, CURVE.start) == datetime(
        2025, 9, 7, 18, 30, tzinfo=UTC
    )


def test_align_keeps_the_time_of_day_when_editions_start_at_different_hours() -> None:
    """The point of aligning on the clock rather than on elapsed time.

    An edition whose recording began at noon must still map 21:00 to 21:00,
    not to 21:00 shifted by the four-hour difference in start times.
    """
    noon_start = datetime(2025, 9, 5, 12, 0, tzinfo=UTC)
    assert align(datetime(2026, 9, 5, 21, 0, tzinfo=UTC), THIS_START, noon_start) == datetime(
        2025, 9, 6, 21, 0, tzinfo=UTC
    )


# ── lookups ──────────────────────────────────────────────────────────


def test_amount_at_interpolates_between_samples() -> None:
    assert amount_at(CURVE, datetime(2025, 9, 5, 17, 0, tzinfo=UTC)) == 1_000_000.0
    # Half way between the 17:00 and 18:00 samples.
    assert amount_at(CURVE, datetime(2025, 9, 5, 17, 30, tzinfo=UTC)) == 2_500_000.0


def test_amount_at_outside_the_curve() -> None:
    assert amount_at(CURVE, datetime(2025, 9, 5, 15, 0, tzinfo=UTC)) is None
    # Past the end the edition was simply over; the final total stands.
    assert amount_at(CURVE, datetime(2025, 9, 9, 0, 0, tzinfo=UTC)) == 16_000_000.0


def test_reached_at_finds_the_first_crossing() -> None:
    assert reached_at(CURVE, 1_000_000) == datetime(2025, 9, 5, 17, 0, tzinfo=UTC)
    assert reached_at(CURVE, 1_000_001) == datetime(2025, 9, 5, 18, 0, tzinfo=UTC)
    assert reached_at(CURVE, 99_000_000) is None


# ── the notification line ────────────────────────────────────────────


def test_being_ahead_of_the_reference_edition() -> None:
    # 4 M€ at day 0 17:00 in 2026; 2025 needed until day 0 18:00.
    line = compare_milestone(CURVE, 4_000_000, datetime(2026, 9, 4, 17, 0, tzinfo=UTC), THIS_START)
    assert line is not None
    assert "d'avance sur 2025" in line
    assert "vendredi 5 septembre à 18 h 00" in line


def test_being_behind_the_reference_edition() -> None:
    line = compare_milestone(CURVE, 4_000_000, datetime(2026, 9, 4, 21, 0, tzinfo=UTC), THIS_START)
    assert line is not None
    assert "de retard sur 2025" in line


def test_a_dead_heat_is_not_dressed_up_as_a_lead() -> None:
    line = compare_milestone(CURVE, 4_000_000, datetime(2026, 9, 4, 18, 1, tzinfo=UTC), THIS_START)
    assert line is not None
    assert "avance" not in line and "retard" not in line


def test_beating_the_reference_edition_outright() -> None:
    line = compare_milestone(CURVE, 20_000_000, datetime(2026, 9, 4, 20, 0, tzinfo=UTC), THIS_START)
    assert line is not None
    assert "Jamais atteint" in line
    assert "16 000 000 €" in line


def test_no_comparison_without_data_or_before_the_marathon() -> None:
    assert (
        compare_milestone(None, 1_000_000, datetime(2026, 9, 4, 20, 0, tzinfo=UTC), THIS_START)
        is None
    )
    # Milestones crossed during the pre-event concert are not comparable.
    assert (
        compare_milestone(CURVE, 1_000_000, datetime(2026, 9, 3, 20, 0, tzinfo=UTC), THIS_START)
        is None
    )


# ── formatting ───────────────────────────────────────────────────────


def test_format_moment_names_the_day_in_french() -> None:
    assert format_moment(datetime(2025, 9, 7, 18, 10, tzinfo=UTC)) == (
        "dimanche 7 septembre à 18 h 10"
    )
    assert format_moment(datetime(2025, 9, 5, 8, 5, tzinfo=UTC)) == "vendredi 5 septembre à 8 h 05"


def test_format_duration_is_coarse_on_purpose() -> None:
    assert format_duration(0) == "0 min"
    assert format_duration(3600) == "1 h"
    assert format_duration(3600 + 20 * 60) == "1 h 20"
    assert format_duration(86400 + 5 * 3600) == "1 j 5 h"
    assert format_duration(-50) == "0 min"


def test_format_euros_uses_space_separators() -> None:
    assert format_euros(16_178_394) == "16 178 394 €"


# ── edition selection ────────────────────────────────────────────────


def test_edition_label_prefers_the_year() -> None:
    assert edition_label("ZEvent 2025") == "2025"
    assert edition_label("Projet Avengers") == "Projet Avengers"
    assert edition_label("") == "l'édition précédente"


def _event(eid: str, name: str, start: str, group: str | None) -> dict:
    return {"id": eid, "name": name, "schedule": {"start": start}, "event_group_id": group}


ZEVENT_GROUP = "019f5bad-f48a-7cda-9817-ffba311f987c"
EVENTS = [
    _event("2026", "ZEvent 2026", "2026-09-03T18:00:00Z", ZEVENT_GROUP),
    _event("2025", "ZEvent 2025", "2025-09-04T10:00:00Z", ZEVENT_GROUP),
    _event("2024", "ZEvent 2024", "2024-09-05T16:00:00Z", ZEVENT_GROUP),
    _event("bop", "Birds Of Prey #6", "2025-11-08T13:00:00Z", None),
]


def test_only_past_editions_of_the_same_series_are_comparable() -> None:
    # Most recent first, and the ungrouped one-off never qualifies — measuring
    # a marathon against a weekend charity stream would be meaningless.
    assert [e["id"] for e in comparable_editions(EVENTS, EVENTS[0])] == ["2025", "2024"]


def test_an_ungrouped_event_has_nothing_to_compare_against() -> None:
    assert comparable_editions(EVENTS, EVENTS[3]) == []


def test_the_oldest_edition_has_no_predecessor() -> None:
    assert comparable_editions(EVENTS, EVENTS[2]) == []


def test_editions_without_a_usable_start_are_skipped_not_compared() -> None:
    """A malformed schedule must not crash the selection.

    Comparing a missing start against a real one raised TypeError before the
    guard existed.
    """
    broken = [
        {"id": "no-schedule", "name": "ZEvent ????", "event_group_id": ZEVENT_GROUP},
        {"id": "bad", "name": "x", "schedule": "nope", "event_group_id": ZEVENT_GROUP},
        {"id": "null", "name": "y", "schedule": {"start": None}, "event_group_id": ZEVENT_GROUP},
        EVENTS[1],
        "not even a dict",
    ]
    assert [e["id"] for e in comparable_editions(broken, EVENTS[0])] == ["2025"]
