"""Tests for ``features.zunivers_ninja.client``.

Payload shape mirrors ZUnivers Ninja's ``GET /api/plan/{pseudo}/discord``
(apps/worker/src/server.ts in LightD31/ZUnivers-Ninja).
"""

import json
from typing import Any

import pytest
from aiohttp import ClientConnectionError

from features.zunivers_ninja import (
    NinjaClient,
    NinjaError,
    NinjaNotFoundError,
    NinjaPlan,
    build_plan_request,
    build_web_url,
    parse_strategies,
)
from features.zunivers_ninja import client as ninja_client


def _payload(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "hash": "abc123",
        "generatedAt": "2026-10-11T08:00:00.000Z",
        "empty": False,
        "player": {
            "name": "alexpresso",
            "displayName": "AlexPresso",
            "discordId": "123456789012345678",
            "avatar": None,
        },
        "commandCount": 2,
        "commands": ["/journa", "/bonus"],
        "gain": {"points": 42, "expected": 0},
        "content": "Conseils pour AlexPresso",
        "embeds": [{"title": "Plan du jour — AlexPresso", "description": "```\n/journa\n```"}],
        "attachment": None,
    }
    data.update(overrides)
    return data


class _FakeResponse:
    def __init__(self, status: int, body: Any = None):
        self.status = status
        self._body = body

    async def json(self, content_type: str | None = "application/json") -> Any:
        if isinstance(self._body, str):
            return json.loads(self._body)
        return self._body

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakeSession:
    def __init__(self, response: _FakeResponse | Exception):
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _client(session: _FakeSession) -> NinjaClient:
    async def factory() -> Any:
        return session

    return NinjaClient("http://zunivers-ninja:8080/", session_factory=factory)


@pytest.fixture(autouse=True)
def _clear_strategies_cache() -> None:
    ninja_client._strategies_cache.clear()


_STRATEGIES = {
    "strategy": "equilibre",
    "strategies": [
        {"id": "equilibre", "label": "Équilibré", "description": "Le plus de points."},
        {"id": "prudent", "label": "Prudent", "description": "Points garantis."},
        {"label": "sans id"},
    ],
}


def test_build_plan_request_quotes_pseudo_and_sets_params() -> None:
    url, params = build_plan_request("http://ninja:8080/", " a/b c ", ruleset="hardcore")
    assert url == "http://ninja:8080/api/plan/a%2Fb%20c/discord"
    assert params == {"ruleset": "HARDCORE"}


def test_build_plan_request_passes_strategy() -> None:
    _, params = build_plan_request("http://ninja:8080", "alex", strategy="prudent")
    assert params == {"strategy": "prudent"}


def test_build_plan_request_omits_defaults() -> None:
    _, params = build_plan_request("http://ninja:8080", "alex")
    assert params == {}


def test_build_web_url_links_the_players_plan() -> None:
    assert build_web_url("https://ninja.example/", "alex.presso") == (
        "https://ninja.example/?u=alex.presso"
    )
    assert build_web_url("https://ninja.example", "a b", hardcore=True) == (
        "https://ninja.example/?u=a+b&mode=hardcore"
    )
    assert build_web_url("https://ninja.example", "alex", strategy="prudent") == (
        "https://ninja.example/?u=alex&strategy=prudent"
    )


def test_from_payload_parses_fields() -> None:
    plan = NinjaPlan.from_payload(_payload())
    assert plan.hash == "abc123"
    assert not plan.empty
    assert plan.command_count == 2
    assert plan.commands == ["/journa", "/bonus"]
    assert plan.player_display_name == "AlexPresso"
    assert plan.discord_id == "123456789012345678"
    assert plan.attachment is None
    assert len(plan.embeds) == 1


def test_from_payload_reads_attachment() -> None:
    plan = NinjaPlan.from_payload(
        _payload(attachment={"name": "conseils.txt", "content": "/journa\n"}, player={})
    )
    assert plan.attachment is not None
    assert plan.attachment.content == "/journa\n"
    assert plan.discord_id is None


def test_from_payload_rejects_missing_hash() -> None:
    data = _payload()
    del data["hash"]
    with pytest.raises(NinjaError):
        NinjaPlan.from_payload(data)


async def test_get_plan_requests_the_discord_route() -> None:
    session = _FakeSession(_FakeResponse(200, _payload()))
    plan = await _client(session).get_plan("alex", ruleset="NORMAL", strategy="vortex")
    assert plan.hash == "abc123"
    call = session.calls[0]
    assert call["url"] == "http://zunivers-ninja:8080/api/plan/alex/discord"
    assert call["params"] == {"ruleset": "NORMAL", "strategy": "vortex"}


async def test_get_plan_raises_not_found_with_ninja_message() -> None:
    body = {"error": "Joueur « nobody » introuvable sur ZUnivers (NORMAL)."}
    session = _FakeSession(_FakeResponse(404, body))
    with pytest.raises(NinjaNotFoundError, match="introuvable sur ZUnivers"):
        await _client(session).get_plan("nobody")


async def test_get_plan_raises_on_server_error() -> None:
    session = _FakeSession(_FakeResponse(502, {"error": "L'API ZUnivers ne répond pas."}))
    with pytest.raises(NinjaError, match="ne répond pas") as excinfo:
        await _client(session).get_plan("alex")
    assert excinfo.value.status == 502


async def test_get_plan_wraps_connection_errors() -> None:
    session = _FakeSession(ClientConnectionError("refused"))
    with pytest.raises(NinjaError, match="ne répond pas"):
        await _client(session).get_plan("alex")


async def test_get_plan_wraps_invalid_json() -> None:
    session = _FakeSession(_FakeResponse(200, "not json"))
    with pytest.raises(NinjaError, match="inattendue"):
        await _client(session).get_plan("alex")


def test_parse_strategies_skips_entries_without_id() -> None:
    strategies = parse_strategies(_STRATEGIES)
    assert [st.id for st in strategies] == ["equilibre", "prudent"]
    assert strategies[1].label == "Prudent"
    assert strategies[1].description == "Points garantis."


def test_parse_strategies_rejects_unexpected_shape() -> None:
    with pytest.raises(NinjaError):
        parse_strategies({"strategy": "equilibre"})


async def test_get_strategies_is_cached() -> None:
    session = _FakeSession(_FakeResponse(200, _STRATEGIES))
    client = _client(session)
    first = await client.get_strategies()
    second = await client.get_strategies()
    assert [st.id for st in second] == [st.id for st in first] == ["equilibre", "prudent"]
    assert len(session.calls) == 1
    assert session.calls[0]["url"] == "http://zunivers-ninja:8080/api/strategies"


async def test_get_strategies_on_older_ninja_is_empty() -> None:
    session = _FakeSession(_FakeResponse(404, {"error": "Route inconnue."}))
    assert await _client(session).get_strategies() == []


async def test_get_strategies_falls_back_to_stale_list(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession(_FakeResponse(200, _STRATEGIES))
    client = _client(session)
    await client.get_strategies()

    monkeypatch.setattr(ninja_client, "_STRATEGIES_TTL", -1.0)  # expire the cache
    session.response = ClientConnectionError("refused")
    assert [st.id for st in await client.get_strategies()] == ["equilibre", "prudent"]


async def test_get_strategies_raises_without_cache() -> None:
    session = _FakeSession(ClientConnectionError("refused"))
    with pytest.raises(NinjaError):
        await _client(session).get_strategies()
