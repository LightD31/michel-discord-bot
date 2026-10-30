"""Unit tests for ``features.vlrgg.normalize``.

Fixtures mirror ``/v2/team?q=schedule`` and ``/v2/match?q=upcoming`` as
returned by a self-hosted vlrggapi on 30 October 2026.
"""

from datetime import UTC, datetime
from typing import Any

import pytest

from features.vlrgg.normalize import (
    enrich_match_from_details,
    expand_round_name,
    normalize_team_match,
    split_event_round,
    upcoming_from_schedule,
)

NOW = datetime(2026, 10, 30, 16, 0, tzinfo=UTC)


def _fixture(match_id: str, event: str, date: str, time: str, team2: str) -> dict[str, Any]:
    return {
        "match_id": match_id,
        "url": f"https://www.vlr.gg/{match_id}/gentle-mates-gc-vs-x",
        "event": event,
        "date": date,
        "time": time,
        "eta": "",
        "team1": {"name": "Gentle Mates GC", "tag": "M8GC", "logo": ""},
        "team2": {"name": team2, "tag": team2[:3].upper(), "logo": ""},
    }


HOMEPAGE = [
    {
        "team1": "Gentle Mates GC",
        "team2": "Shopify Rebellion Gold",
        "match_series": "Main Event: Upper Final",
        "match_event": "Game Changers 2026: Championship São Paulo",
        "unix_timestamp": "2026-10-30 18:00:00",
        "match_page": "https://www.vlr.gg/773435/gentle-mates-gc-vs-shopify-rebellion-gold",
        "match_id": "773435",
    },
    {
        "team1": "KRÜ BLAZE",
        "team2": "MIBR GC",
        "match_series": "Main Event: Lower Round 4",
        "match_event": "Game Changers 2026: Championship São Paulo",
        "unix_timestamp": "2026-10-30 21:00:00",
        "match_page": "https://www.vlr.gg/773443/kru-blaze-vs-mibr-gc",
        "match_id": "773443",
    },
]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("GC 26: ChampionshipUBF", ("GC 26: Championship", "Finale Winner Bracket")),
        ("GC 26: ChampionshipUBSF", ("GC 26: Championship", "Demi-finale Winner Bracket")),
        ("GF", ("", "Grande Finale")),
        ("LR2", ("", "Round 2 Loser Bracket")),
        ("UR1", ("", "Round 1 Winner Bracket")),
        ("W3", ("", "Semaine 3")),
        # An all-caps event name can't be split safely: leave it whole.
        ("VCT 2026: EMEAW3", ("VCT 2026: EMEAW3", "")),
        ("Champions Tour", ("Champions Tour", "")),
        ("", ("", "")),
    ],
)
def test_split_event_round(text: str, expected: tuple[str, str]) -> None:
    assert split_event_round(text) == expected


def test_expand_round_name_keeps_unknown_series() -> None:
    assert expand_round_name("UB SF") == "Demi-finale Winner Bracket"
    assert expand_round_name("R2") == "Round 2"
    assert expand_round_name("Main Event: Upper Final") == "Main Event: Upper Final"


def test_normalize_team_match_splits_glued_event() -> None:
    entry = {
        "match_id": "773433",
        "url": "https://www.vlr.gg/773433/x",
        "event": "GC 26: ChampionshipUBSF",
        "date": "2026/10/27",
        "time": "7:00 pm",
        "team1": {"name": "Gentle Mates GC"},
        "team2": {"name": "MIBR GC"},
        "score": "2:0",
        "result": "win",
    }

    match = normalize_team_match(entry)

    assert match["tournament_name"] == "GC 26: Championship"
    assert match["round_info"] == "Demi-finale Winner Bracket"
    assert match["time_completed"] == "<t:1793124000:f>"  # 19:00 CET = 18:00 UTC


def test_upcoming_drops_recent_results_and_takes_homepage_time() -> None:
    schedule = [
        _fixture("773435", "GC 26: ChampionshipUBF", "2026/10/30", "7:00 pm", "Shopify"),
        _fixture("773433", "GC 26: ChampionshipUBSF", "2026/10/27", "7:00 pm", "MIBR GC"),
        _fixture("773430", "GC 26: ChampionshipUBQF", "2026/10/23", "8:00 pm", "Liquid"),
    ]

    upcoming = upcoming_from_schedule(
        schedule,
        exclude_ids={"773433", "773430"},
        homepage=HOMEPAGE,
        vlr_name="Gentle Mates GC",
        now=NOW,
    )

    assert len(upcoming) == 1
    match = upcoming[0]
    assert match["match_id"] == "773435"
    assert match["team1"] == "Gentle Mates GC"
    assert match["kickoff"] == int(datetime(2026, 10, 30, 18, tzinfo=UTC).timestamp())
    assert match["kickoff_precise"] is True
    assert match["match_event"] == "Game Changers 2026: Championship São Paulo"
    assert match["round_info"] == "Main Event: Upper Final"


def test_upcoming_parses_team_page_time_when_not_on_homepage() -> None:
    schedule = [_fixture("800001", "GC 26: ChampionshipGF", "2026/11/02", "7:00 pm", "G2 Gozen")]

    upcoming = upcoming_from_schedule(
        schedule, exclude_ids=set(), homepage=HOMEPAGE, vlr_name="Gentle Mates GC", now=NOW
    )

    assert len(upcoming) == 1
    # 19:00 Europe/Paris (CET) on 2 Nov 2026 is 18:00 UTC.
    assert upcoming[0]["kickoff"] == int(datetime(2026, 11, 2, 18, tzinfo=UTC).timestamp())
    assert upcoming[0]["match_event"] == "GC 26: Championship"
    assert upcoming[0]["round_info"] == "Grande Finale"


def test_upcoming_is_empty_for_a_team_with_only_recent_results() -> None:
    # FNATIC's team page had no upcoming match: q=schedule returned its 5 last results.
    schedule = [
        _fixture("729680", "LR2", "2026/08/14", "7:30 pm", "Joblife"),
        _fixture("729677", "LR1", "2026/08/13", "8:00 pm", "GIANTX"),
        _fixture("729667", "UR1", "2026/08/07", "7:00 pm", "Enterprise"),
    ]
    ids = {"729680", "729677", "729667"}

    assert (
        upcoming_from_schedule(
            schedule, exclude_ids=ids, homepage=HOMEPAGE, vlr_name="FNATIC", now=NOW
        )
        == []
    )
    # Without the history to compare against, the 3-hour cutoff still drops them.
    assert (
        upcoming_from_schedule(
            schedule, exclude_ids=set(), homepage=HOMEPAGE, vlr_name="FNATIC", now=NOW
        )
        == []
    )


def test_upcoming_falls_back_to_homepage_by_exact_name() -> None:
    upcoming = upcoming_from_schedule(
        [], exclude_ids=set(), homepage=HOMEPAGE, vlr_name="MIBR GC", now=NOW
    )

    assert [m["match_id"] for m in upcoming] == ["773443"]
    assert (
        upcoming_from_schedule([], exclude_ids=set(), homepage=HOMEPAGE, vlr_name="MIBR", now=NOW)
        == []
    )


def test_enrich_swaps_scores_by_team_id() -> None:
    match = {"team1": "Gentle Mates GC", "team2": "MIBR GC", "score1": "?", "score2": "?"}
    match["status"] = "completed"
    details = {
        "event": {
            "name": "Game Changers 2026: Championship São Paulo Main Event: Upper Semifinals",
            "series": "Main Event: Upper Semifinals",
        },
        "teams": [
            {"id": "8050", "name": "MIBR GC", "score": "0"},
            {"id": "21093", "name": "Gentle Mates GC", "score": "2"},
        ],
        "maps": [
            {"map_name": "Ascent", "score": {"team1": 4, "team2": 13}},
            {"map_name": "Haven", "score": {"team1": 11, "team2": 13}},
        ],
    }

    enriched = enrich_match_from_details(match, details, "21093")

    assert enriched["tournament_name"] == "Game Changers 2026: Championship São Paulo"
    assert enriched["round_info"] == "Main Event: Upper Semifinals"
    assert (enriched["score1"], enriched["score2"]) == ("2", "0")
    assert enriched["maps"] == [
        {"map": "Ascent", "score1": 13, "score2": 4},
        {"map": "Haven", "score1": 13, "score2": 11},
    ]
