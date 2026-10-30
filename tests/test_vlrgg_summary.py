"""Unit tests for ``features.vlrgg.summary``.

``DETAILS`` is trimmed from ``/v2/match/details?match_id=773433&light=true``
(MIBR GC 0-2 Gentle Mates GC, Game Changers 2026 Championship).
"""

from features.vlrgg.summary import (
    MapRecord,
    compare_map_pools,
    head_to_head,
    live_round_score,
    map_pool,
    map_scores,
    top_players,
    veto_line,
    veto_steps,
)


def _player(name: str, rating: str, acs: str, k: str, d: str, a: str, agent: str = "") -> dict:
    return {
        "name": name,
        "agent": agent,
        "rating": rating,
        "acs": acs,
        "kills": k,
        "deaths": d,
        "assists": a,
    }


DETAILS = {
    "status": "final",
    "map_vetos": (
        "MIBR.GC ban Split; M8GC ban Lotus; MIBR.GC pick Ascent; M8GC pick Haven; "
        "MIBR.GC ban Sunset; M8GC ban Summit; Abyss remains"
    ),
    "teams": [
        {"id": "8050", "name": "MIBR GC", "score": "0"},
        {"id": "21093", "name": "Gentle Mates GC", "score": "2"},
    ],
    "maps": [
        {
            "map_name": "Ascent",
            "picked_by": "MIBR GC",
            "duration": "51:53",
            "score": {"team1": 4, "team2": 13},
            "players": {
                "team1": [_player("conir", "1.13", "235", "14", "13", "4")],
                "team2": [_player("Marti", "1.80", "340", "22", "12", "11", "Jett")],
            },
        },
        {
            "map_name": "Haven",
            "picked_by": "Gentle Mates GC",
            "duration": "48:01",
            "score": {"team1": 11, "team2": 13},
            "players": {
                "team1": [_player("conir", "0.95", "201", "15", "18", "3")],
                "team2": [_player("Marti", "1.58", "304", "21", "15", "13", "Raze")],
            },
        },
        # Map in progress: vlr.gg shows "-" as its duration.
        {"map_name": "Summit", "duration": "-", "score": {"team1": 2, "team2": 4}},
        # Decider never played: no score, so it isn't listed.
        {"map_name": "Abyss", "score": {"team1": "", "team2": ""}, "players": {}},
        {"map_name": "TBD", "score": {"team1": "", "team2": ""}},
    ],
}


def test_map_scores_lists_played_maps_with_picks() -> None:
    maps = map_scores(DETAILS)

    assert [(m.name, m.score1, m.score2, m.picked_by, m.duration) for m in maps] == [
        ("Ascent", "4", "13", "MIBR GC", "51:53"),
        ("Haven", "11", "13", "Gentle Mates GC", "48:01"),
        ("Summit", "2", "4", "", ""),
    ]
    assert [m.winner for m in maps] == [1, 1, 1]


def test_map_scores_swap_puts_the_other_side_first() -> None:
    maps = map_scores(DETAILS, swap=True)

    assert [(m.score1, m.score2, m.winner) for m in maps] == [
        ("13", "4", 0),
        ("13", "11", 0),
        ("4", "2", 0),
    ]


def test_top_players_averages_rating_and_sums_kda() -> None:
    marti, conir = top_players(DETAILS, n=2)

    assert marti.name == "Marti"
    assert marti.agents == ("Jett", "Raze")
    assert round(marti.rating, 2) == 1.69
    assert marti.acs == 322
    assert (marti.kills, marti.deaths, marti.assists) == (43, 27, 24)
    assert conir.name == "conir"
    assert conir.agents == ()


def test_veto_steps_and_line() -> None:
    steps = veto_steps(DETAILS)

    assert [(s.action, s.map, s.team) for s in steps[:3]] == [
        ("ban", "Split", "MIBR.GC"),
        ("ban", "Lotus", "M8GC"),
        ("pick", "Ascent", "MIBR.GC"),
    ]
    assert steps[-1].action == "decider"
    assert veto_line(DETAILS) == (
        "🚫 Split (MIBR.GC) · 🚫 Lotus (M8GC) · ✅ Ascent (MIBR.GC) · ✅ Haven (M8GC) · "
        "🚫 Sunset (MIBR.GC) · 🚫 Summit (M8GC) · 🎯 Abyss"
    )


def test_veto_ignores_a_header_note_that_is_not_a_veto() -> None:
    assert veto_steps({"map_vetos": "Patch 13.06"}) == []
    assert veto_line({}) == ""


def test_map_pool_skips_unplayed_maps_and_sorts_by_games() -> None:
    stats = [
        {"map": "Bind", "games": 0, "wins": 0, "losses": 0},
        {"map": "Split", "games": 9, "wins": 7, "losses": 2},
        {"map": "Haven", "games": 14, "wins": 8, "losses": 6},
    ]

    pool = map_pool(stats)

    assert pool == [MapRecord("Haven", 14, 8, 6), MapRecord("Split", 9, 7, 2)]
    assert [r.win_pct for r in pool] == [57, 78]
    assert map_pool(stats, min_games=10) == [MapRecord("Haven", 14, 8, 6)]


def test_compare_map_pools_unions_maps_by_combined_games() -> None:
    ours = [MapRecord("Haven", 14, 8, 6), MapRecord("Split", 9, 7, 2)]
    theirs = [MapRecord("Lotus", 18, 12, 6), MapRecord("haven", 17, 11, 6)]

    rows = compare_map_pools(ours, theirs)

    assert [(name, a is not None, b is not None) for name, a, b in rows] == [
        ("haven", True, True),
        ("Lotus", False, True),
        ("Split", True, False),
    ]


def test_head_to_head_matches_the_opponent_exactly() -> None:
    results = [
        {"team2": "G2 Gozen", "match_id": "1"},
        {"team2": "G2 Gozen Academy", "match_id": "2"},
        {"team2": "SK Nebula", "match_id": "3"},
        {"team2": "g2 gozen", "match_id": "4"},
    ]

    assert [m["match_id"] for m in head_to_head(results, "G2 Gozen")] == ["1", "4"]
    assert [m["match_id"] for m in head_to_head(results, "G2 Gozen", n=1)] == ["1"]


def test_live_round_score_adds_the_halves_already_played() -> None:
    # Real live_score segment before half-time: each team only has its current side.
    first_half = {
        "team1_round_ct": "2",
        "team1_round_t": "N/A",
        "team2_round_ct": "N/A",
        "team2_round_t": "3",
    }
    second_half = {
        "team1_round_ct": "7",
        "team1_round_t": "2",
        "team2_round_ct": "3",
        "team2_round_t": "5",
    }

    assert live_round_score(first_half) == (2, 3)
    assert live_round_score(second_half) == (9, 8)
    assert live_round_score({"team1_round_ct": "N/A", "team2_round_t": "1"}) is None
