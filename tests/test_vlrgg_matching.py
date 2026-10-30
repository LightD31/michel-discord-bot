"""Unit tests for ``features.vlrgg.matching`` — attributing matches to a team by id."""

from features.vlrgg.matching import (
    due_for_reminder,
    is_final,
    live_candidates,
    match_id_of,
    same_team_name,
    team_side,
)

DETAILS = {
    "status": "final",
    "teams": [
        {"id": "8050", "name": "MIBR GC", "score": "0", "is_winner": False},
        {"id": "21093", "name": "Gentle Mates GC", "score": "2", "is_winner": True},
    ],
}


def test_team_side_uses_team_ids() -> None:
    assert team_side(DETAILS, "8050") == 0
    assert team_side(DETAILS, "21093") == 1
    assert team_side(DETAILS, "12694") is None  # Gentle Mates, not the GC roster
    assert team_side(DETAILS, None) is None
    assert team_side({}, "21093") is None


def test_same_team_name_is_exact_not_substring() -> None:
    assert same_team_name("G2 Esports", "  g2 esports ")
    assert not same_team_name("G2", "G2 Gozen")
    assert not same_team_name("", "")


def test_match_id_of_prefers_field_then_url() -> None:
    assert match_id_of({"match_id": "773435"}) == "773435"
    assert match_id_of({"match_page": "https://www.vlr.gg/773433/mibr-gc-vs-gentle-mates"}) == (
        "773433"
    )
    assert match_id_of({"url": "https://www.vlr.gg/1/x"}) == "1"
    assert match_id_of({}) == ""


def test_live_candidates_match_by_schedule_id_or_exact_name() -> None:
    segments = [
        {"team1": "G2 Gozen", "team2": "SK Nebula", "match_id": "1"},
        {"team1": "KRÜ Esports", "team2": "G2 Esports", "match_id": "2"},
        {"team1": "TBD", "team2": "TBD", "match_id": "3"},
        {"team1": "Team Vitality", "team2": "FNATIC", "match_id": "4"},
    ]

    found = live_candidates(segments, {"3"}, "G2 Esports")

    # The old substring match on "G2" would also have caught G2 Gozen (id 1).
    assert [seg["match_id"] for seg in found] == ["2", "3"]


def test_is_final_reads_the_match_header_status() -> None:
    assert is_final(DETAILS)
    assert is_final({"status": " FINAL "})
    assert not is_final({"status": "LIVE"})
    assert not is_final({"status": "1h 43m"})
    assert not is_final({})


def test_due_for_reminder_keeps_precise_kickoffs_inside_the_window() -> None:
    upcoming = [
        {"match_id": "soon", "kickoff": 1500, "kickoff_precise": True},
        {"match_id": "edge", "kickoff": 1900, "kickoff_precise": True},
        {"match_id": "later", "kickoff": 1901, "kickoff_precise": True},
        {"match_id": "started", "kickoff": 1000, "kickoff_precise": True},
        {"match_id": "day-only", "kickoff": 1500, "kickoff_precise": False},
        {"match_id": "tbd", "kickoff": None, "kickoff_precise": False},
    ]

    due = due_for_reminder(upcoming, now=1000, window_seconds=900)

    assert [m["match_id"] for m in due] == ["soon", "edge"]
    assert due_for_reminder(upcoming, now=1000, window_seconds=0) == []
