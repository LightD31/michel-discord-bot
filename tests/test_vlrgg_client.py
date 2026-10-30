"""Unit tests for ``features.vlrgg.client``.

Payload shapes mirror the self-hosted https://github.com/axsddlr/vlrggapi V2
routes as of October 2026, where ``/v2/team/matches`` was folded into
``/v2/team?q=matches`` and team match dates split into ``date`` + ``time``.
"""

from typing import Any

from features.vlrgg import client


def _team_match_entry(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "match_id": "773433",
        "url": "https://www.vlr.gg/773433/mibr-gc-vs-gentle-mates-gc-game-changers-2026-ubsf",
        "event": "UBSF",
        "date": "2026/10/27",
        "time": "1:00 pm",
        "team1": {"name": "Gentle Mates GC", "tag": "M8GC", "logo": ""},
        "team2": {"name": "MIBR GC", "tag": "MIBR.GC", "logo": ""},
        "score": "2:0",
        "result": "win",
    }
    entry.update(overrides)
    return entry


def test_normalize_team_match_combines_split_date_and_time() -> None:
    match = client.normalize_team_match(_team_match_entry())

    # 13:00 Europe/Paris on 27 Oct 2026 is CET (UTC+1), i.e. 12:00 UTC.
    assert match["time_completed"] == "<t:1793102400:f>"
    assert match["team1"] == "Gentle Mates GC"
    assert match["team2"] == "MIBR GC"
    assert (match["score1"], match["score2"]) == ("2", "0")
    assert match["round_info"] == "Demi-finale Winner Bracket"
    assert match["status"] == "completed"
    assert match["result"] == "win"
    assert match["match_id"] == "773433"


def test_normalize_team_match_applies_summer_time() -> None:
    match = client.normalize_team_match(_team_match_entry(date="2026/08/14", time="12:30 pm"))

    # CEST (UTC+2): 12:30 Paris is 10:30 UTC.
    assert match["time_completed"] == "<t:1786703400:f>"


def test_normalize_team_match_still_reads_legacy_combined_date() -> None:
    entry = _team_match_entry(date="2026/02/1111:00 am")
    del entry["time"]

    match = client.normalize_team_match(entry)

    assert match["time_completed"] == "<t:1770804000:f>"


async def test_fetch_team_matches_uses_consolidated_team_route(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, str] | None]] = []
    segments = [_team_match_entry()]

    async def fake_request(endpoint: str, params: dict[str, str] | None = None) -> dict:
        calls.append((endpoint, params))
        return {
            "status": "success",
            "data": {"status": 200, "segments": segments, "meta": {"page": 2}},
        }

    monkeypatch.setattr(client, "vlrgg_request", fake_request)

    matches = await client.fetch_team_matches_by_id("21093", page=2)

    assert calls == [("v2/team", {"id": "21093", "q": "matches", "page": "2"})]
    assert matches == segments


async def test_fetch_match_details_requests_light_payload(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, str] | None]] = []
    segment = {
        "match_id": "773433",
        "event": {"name": "Game Changers 2026 Championship", "series": "Upper Semifinals"},
        "teams": [{"name": "MIBR GC", "score": "0"}, {"name": "Gentle Mates GC", "score": "2"}],
        "maps": [],
    }

    async def fake_request(endpoint: str, params: dict[str, str] | None = None) -> dict:
        calls.append((endpoint, params))
        return {"status": "success", "data": {"status": 200, "segments": [segment]}}

    monkeypatch.setattr(client, "vlrgg_request", fake_request)

    details = await client.fetch_match_details("773433")

    assert calls == [("v2/match/details", {"match_id": "773433", "light": "true"})]
    assert details == segment
