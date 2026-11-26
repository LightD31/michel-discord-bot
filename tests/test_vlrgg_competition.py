"""The competition a tracked team plays: its event, its standings or its bracket.

Payloads are trimmed from the self-hosted VLR.gg API in November 2026:
``/v2/events?q=live``, ``/v2/event/3233`` (EMEA Kickoff Europe Qualifier
Playoffs, Swiss stage), ``/v2/events/matches?event_id=3179`` (NA Qualifier
Playoffs, double elimination).
"""

from typing import Any

import pytest
from interactions import Embed

from extensions.vlrgg import VlrggExtension
from extensions.vlrgg._common import MAX_SHOWN_MATCHES, ScheduleDisplay
from extensions.vlrgg.embeds import fit_v2
from features.vlrgg import client
from features.vlrgg.competition import (
    BracketMatch,
    BracketRound,
    Competition,
    StandingRow,
    StandingTable,
    bracket_rounds,
    candidate_events,
    competition_view,
    reference_urls,
    round_label,
    stage_of,
    stage_slug,
    standings_tables,
)
from src.discord_ext.look import card

EVENTS = [
    {
        "event_id": "3000",
        "url_path": "https://www.vlr.gg/event/3000/vct-2027-emea-kickoff",
    },
    {
        "event_id": "3233",
        "url_path": "https://www.vlr.gg/event/3233/vct-2027-emea-kickoff-europe-qualifier-playoffs",
    },
    {
        "event_id": "3177",
        "url_path": "https://www.vlr.gg/event/3177/vct-2027-emea-kickoff-t-rkiye-qualifier",
    },
]
MATCH_URL = (
    "https://www.vlr.gg/794456/eintracht-frankfurt-vs-giantx-"
    "vct-2027-emea-kickoff-europe-qualifier-playoffs-swiss-stage-r2-1-0"
)


def _match(
    match_id: str,
    stage: str,
    series: str,
    teams: tuple[str, str],
    score: tuple[str, str] = ("–", "–"),
    status: str = "Upcoming",
) -> dict[str, Any]:
    won = (score[0] > score[1], score[1] > score[0]) if status == "Completed" else (False, False)
    return {
        "match_id": match_id,
        "url": f"https://www.vlr.gg/{match_id}/x",
        "status": status,
        "stage": stage,
        "event_series": series,
        "team1": {"name": teams[0], "score": score[0], "is_winner": won[0]},
        "team2": {"name": teams[1], "score": score[1], "is_winner": won[1]},
    }


STANDINGS = [
    {
        "stage": "Group Stage",
        "group": "Group A",
        "columns": ["Team", "W", "L", "T", "MAP", "RND", "Δ"],
        "rows": [
            {"Team": "Enterprise Esports", "W": "1", "L": "0", "MAP": "2/0", "Δ": "+16"},
            {"Team": "GIANTX", "W": "1", "L": "0", "MAP": "2/0", "Δ": "+7"},
        ],
    },
    {
        "stage": "Group Stage",
        "group": "Group B",
        "columns": ["Team", "W", "L"],
        "rows": [{"Team": "MIR", "W": "0", "L": "1"}],
    },
]


# ── Finding the event ────────────────────────────────────────────────────────


def test_the_most_precise_event_slug_comes_first() -> None:
    assert candidate_events(MATCH_URL, EVENTS) == ["3233", "3000"]


def test_a_slug_matches_whole_words_only() -> None:
    url = "https://www.vlr.gg/1/a-vs-b-vct-2027-emea-kickoffs-playoffs"
    assert candidate_events(url, EVENTS) == []
    assert candidate_events("", EVENTS) == []


def test_the_stage_comes_from_the_event_matches() -> None:
    matches = [_match("794456", "Swiss Stage", "Round 2 (1-0)", ("SGE", "GX"))]

    assert stage_of("794456", matches) == "Swiss Stage"
    assert stage_of("1", matches) is None


def test_a_stage_name_gives_its_slug() -> None:
    stages = [{"name": "Playoffs", "slug": "playoffs"}, {"name": "Swiss Stage", "slug": "swiss"}]

    assert stage_slug("swiss stage", stages) == "swiss"
    assert stage_slug("Week 1", stages) == ""


def test_the_team_is_placed_by_its_live_then_next_then_last_match() -> None:
    data = {
        "live": [{"match_page": "https://www.vlr.gg/3/c"}],
        "upcoming": [{"match_id": "2", "match_page": "https://www.vlr.gg/2/b"}, {"match_id": "9"}],
        "results": [{"match_id": "1", "match_page": "https://www.vlr.gg/1/a"}],
    }

    assert [mid for mid, _ in reference_urls(data)] == ["3", "2", "1"]


def test_an_unknown_view_is_off() -> None:
    assert competition_view("bracket") == "bracket"
    assert competition_view("tree") == "off"
    assert competition_view(None) == "off"


# ── Standings and bracket ────────────────────────────────────────────────────


def test_standings_keep_the_team_s_group() -> None:
    (table,) = standings_tables(STANDINGS, "giantx")

    assert table.title == "Group A"
    assert [(r.rank, r.team, r.wins, r.losses) for r in table.rows] == [
        (1, "Enterprise Esports", "1", "0"),
        (2, "GIANTX", "1", "0"),
    ]
    assert table.rows[1].maps == "2/0" and table.rows[1].rounds_diff == "+7"


def test_standings_show_every_group_when_the_team_is_in_none() -> None:
    assert [t.title for t in standings_tables(STANDINGS, "Fnatic")] == ["Group A", "Group B"]


def test_the_bracket_puts_upper_then_lower_then_the_finals() -> None:
    matches = [
        _match("1", "Playoffs", "Upper Quarterfinals", ("Cloud9", "QoR"), ("2", "0"), "Completed"),
        _match("2", "Playoffs", "Lower Round 1", ("QoR", "TBD")),
        _match("3", "Playoffs", "Upper Semifinals", ("Cloud9", "TBD")),
        _match("4", "Playoffs", "Grand Final", ("TBD", "TBD")),
        _match("5", "Playoffs", "Lower Final", ("TBD", "TBD")),
        _match("6", "Swiss Stage", "Round 1 (0-0)", ("Cloud9", "ENVY"), ("2", "1"), "Completed"),
    ]

    rounds = bracket_rounds(matches, "Playoffs")

    assert [r.name for r in rounds] == [
        "Upper Quarterfinals",
        "Upper Semifinals",
        "Lower Round 1",
        "Lower Final",
        "Grand Final",
    ]
    first = rounds[0].matches[0]
    assert (first.team1, first.score1, first.winner, first.played) == ("Cloud9", "2", 0, True)
    assert not rounds[1].matches[0].played


@pytest.mark.parametrize(
    ("series", "label"),
    [
        ("Upper Quarterfinals", "Quarts de finale · Winner Bracket"),
        ("Lower Round 2", "Round 2 · Loser Bracket"),
        ("Grand Final", "Grande Finale"),
        ("Round 2 (1-0)", "Round 2 (1-0)"),
    ],
)
def test_round_labels_are_french(series: str, label: str) -> None:
    assert round_label(series) == label


# ── The client ───────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _fresh_caches():
    client._cache.clear()
    yield
    client._cache.clear()


def _segments(items: Any) -> dict[str, Any]:
    return {"status": "success", "data": {"status": 200, "segments": items}}


def _fake_api(calls: list[tuple[str, dict[str, str]]], stage_matches: list[dict[str, Any]]):
    async def fake_request(endpoint: str, params: dict[str, str] | None = None) -> dict:
        params = params or {}
        calls.append((endpoint, params))
        if endpoint == "v2/events":
            return _segments(EVENTS if params["q"] == "live" else [])
        if endpoint == "v2/events/matches":
            if params["event_id"] == "3233":
                return _segments(stage_matches)
            return _segments([])
        if endpoint == "v2/event/3233":
            stages = [
                {"name": "Playoffs", "slug": "playoffs", "active": True},
                {"name": "Group Stage", "slug": "group-stage", "active": False},
            ]
            standings = STANDINGS if params.get("stage") == "group-stage" else []
            event = {"name": "EMEA Kickoff", "logo": "//owcdn.net/x.png"}
            return _segments({"event": event, "stages": stages, "standings": standings})
        raise AssertionError(f"unexpected request {endpoint} {params}")

    return fake_request


async def test_standings_of_a_past_stage_are_asked_by_its_slug(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, str]]] = []
    matches = [_match("794456", "Group Stage", "Round 2", ("Eintracht Frankfurt", "GIANTX"))]
    monkeypatch.setattr(client, "vlrgg_request", _fake_api(calls, matches))
    data = {"upcoming": [{"match_id": "794456", "match_page": MATCH_URL}]}

    competition = await client.fetch_team_competition(data, "standings", "GIANTX")

    assert competition is not None
    assert (competition.event_id, competition.name, competition.stage) == (
        "3233",
        "EMEA Kickoff",
        "Group Stage",
    )
    assert competition.url == EVENTS[1]["url_path"]
    assert competition.view == "standings"
    assert [t.title for t in competition.tables] == ["Group A"]
    assert ("v2/event/3233", {"include": "event,stages,standings", "stage": "group-stage"}) in calls
    # The broader "vct-2027-emea-kickoff" event is never asked: the precise one held the match.
    assert ("v2/events/matches", {"event_id": "3000"}) not in calls


async def test_a_stage_without_standings_is_shown_as_a_bracket(monkeypatch) -> None:
    matches = [_match("794456", "Playoffs", "Upper Final", ("Eintracht Frankfurt", "GIANTX"))]
    monkeypatch.setattr(client, "vlrgg_request", _fake_api([], matches))
    data = {"upcoming": [{"match_id": "794456", "match_page": MATCH_URL}]}

    competition = await client.fetch_team_competition(data, "standings", "GIANTX")

    assert competition is not None
    assert competition.view == "bracket" and not competition.tables
    assert [r.name for r in competition.rounds] == ["Upper Final"]


async def test_no_competition_when_off_or_unknown(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, str]]] = []
    monkeypatch.setattr(client, "vlrgg_request", _fake_api(calls, []))
    data = {"upcoming": [{"match_id": "794456", "match_page": MATCH_URL}]}

    assert await client.fetch_team_competition(data, "off") is None
    assert calls == []
    # The match is in none of the candidate events' match lists.
    assert await client.fetch_team_competition(data, "bracket") is None


@pytest.mark.parametrize(
    ("endpoint", "params", "ttl"),
    [
        ("v2/events", {"q": "upcoming"}, 300),
        ("v2/event/3233", {"include": "standings"}, 120),
        ("v2/events/matches", {"event_id": "3233"}, 60),
    ],
)
def test_event_cache_ttl(endpoint: str, params: dict[str, str], ttl: int) -> None:
    assert client._get_ttl(endpoint, params) == ttl


# ── Display settings ─────────────────────────────────────────────────────────


def test_display_settings_are_bounded_like_the_schema() -> None:
    display = ScheduleDisplay.from_config(
        {"pastMatches": 99, "upcomingMatches": -3, "competitionView": "bracket"}
    )

    assert (display.past, display.upcoming, display.competition) == (
        MAX_SHOWN_MATCHES,
        0,
        "bracket",
    )
    assert ScheduleDisplay.from_config({"pastMatches": "x"}) == ScheduleDisplay()


def _ext() -> VlrggExtension:
    return object.__new__(VlrggExtension)


def _team_data(results: int, upcoming: int) -> dict[str, Any]:
    return {
        "profile": {"name": "GIANTX"},
        "results": [
            {"team1": "GIANTX", "team2": f"R{i}", "score1": "2", "score2": "0", "result": "win"}
            for i in range(results)
        ],
        "upcoming": [{"team1": "GIANTX", "team2": f"U{i}"} for i in range(upcoming)],
    }


def _match_fields(embed: Embed) -> list[str]:
    return [f.name for f in embed.fields if f.name != "​"]


def test_the_schedule_shows_the_configured_number_of_matches() -> None:
    past, upcoming = _ext()._build_vlrgg_embeds(
        _team_data(8, 8), "GIANTX", ScheduleDisplay(past=2, upcoming=3)
    )

    assert len(_match_fields(past)) == 2
    assert len(_match_fields(upcoming)) == 3


def test_zero_matches_leaves_their_card_out() -> None:
    (upcoming,) = _ext()._build_vlrgg_embeds(
        _team_data(8, 8), "GIANTX", ScheduleDisplay(past=0, upcoming=1)
    )

    assert upcoming.title == "Prochains matchs de GIANTX"


def test_the_standings_card_marks_the_team() -> None:
    table = StandingTable(
        "Swiss Stage",
        [
            StandingRow(1, "Enterprise Esports", "1", "0", "2/0", "+16"),
            StandingRow(2, "GIANTX", "1", "0"),
        ],
    )
    competition = Competition(
        "3233", "EMEA Kickoff", "Swiss Stage", view="standings", tables=[table]
    )

    *_, embed = _ext()._build_vlrgg_embeds(
        _team_data(1, 0), "GIANTX", ScheduleDisplay(competition="standings"), competition
    )

    assert embed.title == "🏆 EMEA Kickoff"
    assert embed.description == "Swiss Stage · Classement"
    (field,) = embed.fields
    assert field.value.splitlines() == [
        "` 1.` Enterprise Esports — 1-0 · maps 2/0 · Δ +16",
        "` 2.` **GIANTX** ⭐ — 1-0",
    ]


def test_the_bracket_card_lists_each_round() -> None:
    rounds = [
        BracketRound("Upper Final", [BracketMatch("GIANTX", "FNATIC", "2", "1", 0, "Completed")]),
        BracketRound("Grand Final", [BracketMatch("GIANTX", "TBD", "", "", None, "Upcoming")]),
        BracketRound("Lower Final", [BracketMatch("KC", "TH", "0", "0", None, "LIVE")]),
    ]
    competition = Competition("1", "Masters", "Playoffs", view="bracket", rounds=rounds)

    embed = _ext()._build_competition_embed(competition, "GIANTX")

    assert embed is not None
    assert [(f.name, f.value) for f in embed.fields] == [
        ("Finale · Winner Bracket", "**GIANTX** **2**-1 FNATIC ⭐"),
        ("Grande Finale", "GIANTX vs TBD ⭐"),
        ("Finale · Loser Bracket", "🔴 KC 0-0 TH"),
    ]


def test_old_rounds_without_the_team_give_way_first() -> None:
    played = [
        BracketMatch(f"Long Team Name {i}", f"Other Long Name {i}", "2", "0", 0, "Completed")
        for i in range(8)
    ]
    rounds = [BracketRound(f"Round {n}", list(played)) for n in range(1, 8)]
    rounds.append(
        BracketRound("Round 8", [BracketMatch("GIANTX", "MIR", "", "", None, "Upcoming")])
    )
    competition = Competition("1", "Swiss", "Swiss Stage", view="bracket", rounds=rounds)

    embed = _ext()._build_competition_embed(competition, "GIANTX")

    assert embed is not None
    names = [f.name for f in embed.fields]
    assert names[-1] == "Round 8"
    assert "Round 1" not in names and len(names) < 8


def test_the_schedule_is_trimmed_to_fit_one_message() -> None:
    embed = card("moduleVlrgg", title="Derniers matchs")
    for i in range(10):
        embed.add_field(name=f"Match {i}", value="x" * 500, inline=True)

    (container,) = fit_v2([embed], limit=2000)

    assert 1 <= len(embed.fields) < 10
    assert _text(container) <= 2000


def _text(component: Any) -> int:
    own = len(getattr(component, "content", None) or "")
    return own + sum(_text(c) for c in getattr(component, "components", None) or [])
