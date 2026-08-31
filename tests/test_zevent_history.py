"""Unit tests for ``features.zevent.history``.

Shapes come from the community project's metrics cache
(``metrics/{event}/global.json``): a cumulative donation curve sampled every
ten minutes, in euros, while the file's top-level total is in centimes.
"""

from features.zevent.history import (
    amount_at,
    comparable_editions,
    compare_milestone,
    edition_label,
    elapsed_to_reach,
    format_duration,
    parse_metrics,
)

HOUR = 3_600_000  # milliseconds, the unit the metrics file uses


def _payload(values: list[float], *, origin: int = 1_757_088_000_000, step: int = HOUR) -> dict:
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


# ── parsing ──────────────────────────────────────────────────────────


def test_parses_into_elapsed_seconds_from_the_first_sample() -> None:
    assert CURVE is not None
    assert CURVE.label == "2025"
    assert CURVE.points[0] == (0.0, 0.0)
    assert CURVE.points[1] == (3600.0, 1_000_000.0)
    assert CURVE.total == 16_000_000.0
    assert CURVE.duration == 4 * 3600.0


def test_unsorted_labels_are_ordered_before_anchoring() -> None:
    """The 2024 file ships its labels reversed — anchoring blind would break."""
    payload = _payload([0, 1_000_000, 4_000_000])
    block = payload["graph"]["donations"]["all"]
    block["labels"].reverse()
    block["values"].reverse()

    curve = parse_metrics(payload, "2024")
    assert curve is not None
    assert curve.points[0] == (0.0, 0.0)
    assert curve.total == 4_000_000.0


def test_unusable_payloads_disable_the_comparison_rather_than_raising() -> None:
    assert parse_metrics(None, "x") is None
    assert parse_metrics({}, "x") is None
    assert parse_metrics({"graph": "nope"}, "x") is None
    assert parse_metrics({"graph": {"donations": {"all": {}}}}, "x") is None
    # A single point cannot describe a curve.
    assert parse_metrics(_payload([1_000_000]), "x") is None
    # Non-numeric entries are skipped, not coerced.
    payload = _payload([0, 1_000_000, 4_000_000])
    payload["graph"]["donations"]["all"]["values"] = [0, "beaucoup", 4_000_000]
    curve = parse_metrics(payload, "x")
    assert curve is not None
    assert len(curve.points) == 2


# ── lookups ──────────────────────────────────────────────────────────


def test_amount_at_interpolates_between_samples() -> None:
    assert CURVE is not None
    assert amount_at(CURVE, 0) == 0.0
    assert amount_at(CURVE, 3600) == 1_000_000.0
    # Half way between the 1 h and 2 h samples.
    assert amount_at(CURVE, 5400) == 2_500_000.0


def test_amount_at_outside_the_curve() -> None:
    assert CURVE is not None
    assert amount_at(CURVE, -1) is None
    # Past the end the edition was simply over; the final total stands.
    assert amount_at(CURVE, 10 * 3600) == 16_000_000.0


def test_elapsed_to_reach_finds_the_first_crossing() -> None:
    assert CURVE is not None
    assert elapsed_to_reach(CURVE, 0) == 0.0
    assert elapsed_to_reach(CURVE, 1_000_000) == 3600.0
    assert elapsed_to_reach(CURVE, 1_000_001) == 2 * 3600.0
    assert elapsed_to_reach(CURVE, 99_000_000) is None


# ── the notification line ────────────────────────────────────────────


def test_being_ahead_of_the_reference_edition() -> None:
    assert CURVE is not None
    line = compare_milestone(CURVE, 4_000_000, elapsed_now=3600)
    assert line is not None
    assert "d'avance" in line and "2025" in line


def test_being_behind_the_reference_edition() -> None:
    assert CURVE is not None
    line = compare_milestone(CURVE, 4_000_000, elapsed_now=5 * 3600)
    assert line is not None
    assert "de retard" in line


def test_a_dead_heat_is_not_dressed_up_as_a_lead() -> None:
    assert CURVE is not None
    line = compare_milestone(CURVE, 4_000_000, elapsed_now=2 * 3600 + 60)
    assert line is not None
    assert "avance" not in line and "retard" not in line


def test_beating_the_reference_edition_outright() -> None:
    assert CURVE is not None
    line = compare_milestone(CURVE, 20_000_000, elapsed_now=3600)
    assert line is not None
    assert "Jamais atteint" in line
    assert "16 000 000 €" in line


def test_no_comparison_before_the_marathon_or_without_data() -> None:
    assert CURVE is not None
    assert compare_milestone(None, 1_000_000, 3600) is None
    # Milestones crossed during the pre-event concert are not comparable.
    assert compare_milestone(CURVE, 1_000_000, -60) is None


# ── formatting and edition selection ─────────────────────────────────


def test_format_duration_is_coarse_on_purpose() -> None:
    assert format_duration(0) == "0 min"
    assert format_duration(90) == "1 min"
    assert format_duration(3600) == "1 h"
    assert format_duration(3600 + 20 * 60) == "1 h 20"
    assert format_duration(86400) == "1 j"
    assert format_duration(86400 + 5 * 3600) == "1 j 5 h"
    assert format_duration(-50) == "0 min"


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
    tracked = EVENTS[0]
    picked = [e["id"] for e in comparable_editions(EVENTS, tracked)]

    # Most recent first, and the ungrouped one-off never qualifies — measuring
    # a marathon against a weekend charity stream would be meaningless.
    assert picked == ["2025", "2024"]


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
    picked = comparable_editions(broken, EVENTS[0])
    assert [e["id"] for e in picked] == ["2025"]
