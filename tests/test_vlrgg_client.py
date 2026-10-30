"""Unit tests for ``features.vlrgg.client``.

Payload shapes mirror the self-hosted https://github.com/axsddlr/vlrggapi V2
routes as of October 2026, where ``/v2/team/matches`` was folded into
``/v2/team?q=matches`` and team match dates split into ``date`` + ``time``.
"""

from typing import Any

import pytest

from features.vlrgg import client


@pytest.fixture(autouse=True)
def _fresh_caches():
    client._cache.clear()
    client._final_details.clear()
    yield
    client._cache.clear()
    client._final_details.clear()


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


async def test_finished_match_details_are_memoized(monkeypatch) -> None:
    calls: list[str] = []
    statuses = {"1": "final", "2": "LIVE"}

    async def fake_request(endpoint: str, params: dict[str, str] | None = None) -> dict:
        match_id = (params or {})["match_id"]
        calls.append(match_id)
        segment = {"match_id": match_id, "status": statuses[match_id], "teams": []}
        return {"status": "success", "data": {"status": 200, "segments": [segment]}}

    monkeypatch.setattr(client, "vlrgg_request", fake_request)

    for _ in range(3):
        await client.fetch_match_details("1")
        await client.fetch_match_details("2")

    # A final match is fetched once; a live one goes back to the (cached) API each time.
    assert calls.count("1") == 1
    assert calls.count("2") == 3


@pytest.mark.parametrize(
    ("endpoint", "params", "ttl"),
    [
        ("v2/team", {"id": "1"}, 1800),
        ("v2/team", {"id": "1", "q": "stats", "date_start": "2026-05-03"}, 1800),
        ("v2/team", {"id": "1", "q": "matches", "page": "1"}, 120),
        ("v2/team", {"id": "1", "q": "schedule"}, 120),
        ("v2/match/details", {"match_id": "1", "light": "true"}, 30),
        ("v2/match", {"q": "live_score"}, 10),
        ("v2/match", {"q": "upcoming"}, 30),
        ("v2/rankings", {"region": "eu"}, 3600),
        ("v2/search", {"q": "gentle"}, 300),
    ],
)
def test_cache_ttl_per_endpoint(endpoint: str, params: dict[str, str], ttl: int) -> None:
    assert client._get_ttl(endpoint, params) == ttl


def test_cache_drops_its_oldest_half_when_full(monkeypatch) -> None:
    monkeypatch.setattr(client, "_CACHE_MAX_ENTRIES", 4)

    for i in range(5):
        client._cache_store(f"k{i}", {"i": i})

    assert sorted(client._cache) == ["k3", "k4"]


async def test_sliding_window_limiter_waits_for_the_oldest_slot() -> None:
    now = [0.0]
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    limiter = client.SlidingWindowLimiter(2, 60.0, clock=lambda: now[0], sleep=fake_sleep)

    await limiter.acquire()
    now[0] = 10.0
    await limiter.acquire()
    await limiter.acquire()  # third in the window: waits until the first leaves it

    assert sleeps == [50.0]
    assert now[0] == 60.0


async def test_only_the_shared_20_per_minute_routes_are_throttled(monkeypatch) -> None:
    throttled: list[str] = []

    class RecordingLimiter:
        async def acquire(self, limit: int | None = None) -> None:
            throttled.append(limit)

    async def fake_fetch(url: str, **kwargs: Any) -> dict:
        return {"status": "success", "data": {"segments": []}}

    monkeypatch.setattr(client, "_rate_limited", RecordingLimiter())
    monkeypatch.setattr(client, "fetch", fake_fetch)
    monkeypatch.setattr(client, "get_api_url", lambda: "https://vlr.example")
    monkeypatch.setattr(client, "get_rate_limit", lambda: 40)

    await client.vlrgg_request("v2/team", {"id": "1", "q": "matches"})
    await client.vlrgg_request("v2/match/details", {"match_id": "1"})
    await client.vlrgg_request("v2/match", {"q": "live_score"})
    await client.vlrgg_request("v2/search", {"q": "g2"})
    await client.vlrgg_request("v2/team", {"id": "1", "q": "matches"})  # cache hit

    assert throttled == [40, 40]


async def test_fetch_all_team_data_attributes_matches_by_id(monkeypatch) -> None:
    team_id = "11058"  # G2 Esports
    details_requested: list[str] = []

    def segments(items: Any) -> dict:
        return {"status": "success", "data": {"status": 200, "segments": items}}

    def details(match_id: str, status: str, ids: tuple[str, str]) -> dict:
        teams = [
            {"id": ids[0], "name": "G2 Esports", "score": "2", "is_winner": True},
            {"id": ids[1], "name": "Opponent", "score": "1", "is_winner": False},
        ]
        return segments([{"match_id": match_id, "status": status, "teams": teams, "maps": []}])

    def fixture(match_id: str, date: str) -> dict:
        return {
            "match_id": match_id,
            "url": f"https://www.vlr.gg/{match_id}/x",
            "event": "GF",
            "date": date,
            "time": "7:00 pm",
            "team1": {"name": "G2 Esports"},
            "team2": {"name": "Opponent"},
        }

    async def fake_request(endpoint: str, params: dict[str, str] | None = None) -> dict:
        params = params or {}
        if endpoint == "v2/team":
            q = params.get("q", "")
            if q == "":
                return segments([{"id": team_id, "name": "G2 Esports", "logo": ""}])
            if q == "matches":
                entry = {
                    "match_id": "100",
                    "url": "https://www.vlr.gg/100/x",
                    "event": "GF",
                    "date": "2026/10/01",
                    "time": "7:00 pm",
                    "team1": {"name": "G2 Esports"},
                    "team2": {"name": "Opponent"},
                    "score": "2:1",
                    "result": "win",
                }
                return segments([entry])
            if q == "schedule":
                # The recent result (100) is mixed in with the real fixture (200).
                return segments([fixture("200", "2099/01/01"), fixture("100", "2026/10/01")])
        if endpoint == "v2/match" and params["q"] == "upcoming":
            return segments([])
        if endpoint == "v2/match" and params["q"] == "live_score":
            return segments(
                [
                    {"team1": "G2 Gozen", "team2": "SK Nebula", "match_id": "300"},
                    {"team1": "G2 Esports", "team2": "LOUD", "match_id": "400"},
                    {"team1": "G2 Esports", "team2": "Homonym", "match_id": "500"},
                ]
            )
        if endpoint == "v2/match/details":
            match_id = params["match_id"]
            details_requested.append(match_id)
            if match_id == "100":
                return details(match_id, "final", (team_id, "1"))
            if match_id == "400":
                return details(match_id, "LIVE", ("2", team_id))
            if match_id == "500":
                return details(match_id, "LIVE", ("998", "999"))  # same name, other team
        raise AssertionError(f"unexpected request {endpoint} {params}")

    monkeypatch.setattr(client, "vlrgg_request", fake_request)

    data = await client.fetch_all_team_data(team_id, "G2")

    assert data["profile"]["name"] == "G2 Esports"
    assert [m["match_id"] for m in data["results"]] == ["100"]
    assert data["results"][0]["round_info"] == "Grande Finale"
    assert [m["match_id"] for m in data["upcoming"]] == ["200"]
    assert [m["match_id"] for m in data["live"]] == ["400"]
    assert "300" not in details_requested  # G2 Gozen is never even considered


async def test_hold_pauses_the_limiter_after_a_429() -> None:
    now = [0.0]
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    limiter = client.SlidingWindowLimiter(18, 60.0, clock=lambda: now[0], sleep=fake_sleep)
    limiter.hold(20.0)
    limiter.hold(5.0)  # a shorter hold never shortens the current one

    await limiter.acquire()

    assert sleeps == [20.0]


async def test_a_429_holds_the_shared_budget(monkeypatch) -> None:
    holds: list[float] = []

    class RecordingLimiter:
        async def acquire(self, limit: int | None = None) -> None:
            pass

        def hold(self, seconds: float) -> None:
            holds.append(seconds)

    async def rate_limited_fetch(url: str, **kwargs: Any) -> dict:
        raise client.HttpError("Failed", url=url, status=429)

    monkeypatch.setattr(client, "_rate_limited", RecordingLimiter())
    monkeypatch.setattr(client, "fetch", rate_limited_fetch)
    monkeypatch.setattr(client, "get_api_url", lambda: "https://vlr.example")

    assert await client.vlrgg_request("v2/match/details", {"match_id": "1"}) == {}
    assert await client.vlrgg_request("v2/match", {"q": "live_score"}) == {}

    assert holds == [client._RATE_LIMIT_BACKOFF_SECONDS]


class _FakeConfigStore:
    def __init__(self, section: dict[str, Any]) -> None:
        self._data = {"config": {"vlrgg": section}}

    def get(self) -> dict[str, Any]:
        return self._data


@pytest.mark.parametrize(
    ("section", "expected"),
    [
        ({}, client.DEFAULT_RATE_LIMIT_PER_MINUTE),
        ({"vlrggRateLimitPerMinute": 60}, 60),
        ({"vlrggRateLimitPerMinute": "45"}, 45),
        ({"vlrggRateLimitPerMinute": 0}, 0),
        ({"vlrggRateLimitPerMinute": -3}, 0),
        ({"vlrggRateLimitPerMinute": "beaucoup"}, client.DEFAULT_RATE_LIMIT_PER_MINUTE),
    ],
)
def test_rate_limit_is_read_from_the_global_section(
    monkeypatch, section: dict[str, Any], expected: int
) -> None:
    monkeypatch.setattr(client, "config_store", _FakeConfigStore(section))

    assert client.get_rate_limit() == expected


async def test_a_zero_rate_limit_disables_the_throttle(monkeypatch) -> None:
    throttled: list[int | None] = []

    class RecordingLimiter:
        async def acquire(self, limit: int | None = None) -> None:
            throttled.append(limit)

    async def fake_fetch(url: str, **kwargs: Any) -> dict:
        return {"status": "success", "data": {"segments": []}}

    monkeypatch.setattr(client, "_rate_limited", RecordingLimiter())
    monkeypatch.setattr(client, "fetch", fake_fetch)
    monkeypatch.setattr(client, "get_api_url", lambda: "https://vlr.example")
    monkeypatch.setattr(client, "get_rate_limit", lambda: 0)

    await client.vlrgg_request("v2/match/details", {"match_id": "1"})

    assert throttled == []


async def test_acquire_uses_the_limit_it_is_given() -> None:
    now = [0.0]
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    limiter = client.SlidingWindowLimiter(1, 60.0, clock=lambda: now[0], sleep=fake_sleep)

    for _ in range(3):
        await limiter.acquire(3)  # a raised limit lets all three through at once
    await limiter.acquire(3)

    assert sleeps == [60.0]
