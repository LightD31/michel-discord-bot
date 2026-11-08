"""The dashboard's list of the messages the bot keeps up to date in a guild.

Mounted on a bare FastAPI app with a stub context: the per-guild check, the
listing and the republish dispatch, without an OAuth session or a bot.
"""

from dataclasses import dataclass

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import src.webui.routes.bot_messages as routes
from src.core.errors import DatabaseError
from src.core.message_refs import MessageRef


@dataclass
class _Session:
    username: str = "admin"
    user_id: str = "42"


class _Ctx:
    """Minimal stand-in for WebUIContext: guild admin of guild 1 only."""

    bot = object()

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def require_guild_admin(self, request, guild_id):  # noqa: ARG002 — parity
        if str(guild_id) != "1":
            raise HTTPException(status_code=403, detail="Accès réservé")
        return _Session()

    async def call_bot(self, func, *args, what, **kwargs):
        self.calls.append((func, args, what))
        return await func(*args, **kwargs)


class _FakeRepo:
    refs = [MessageRef("satisfactory", "10", "100", "Statut Satisfactory")]
    failing = False

    def __init__(self, guild_id):
        self.guild_id = guild_id

    async def list(self):
        if _FakeRepo.failing:
            raise DatabaseError("mongo down")
        return list(_FakeRepo.refs)


@pytest.fixture
def setup(monkeypatch):
    _FakeRepo.failing = False
    republished: list[tuple] = []

    async def fake_republish(bot, guild_id, slot, *, logger=None):
        republished.append((guild_id, slot))
        return slot == "satisfactory"

    monkeypatch.setattr(routes, "MessageRefRepository", _FakeRepo)
    monkeypatch.setattr(routes, "republish_persistent_message", fake_republish)
    ctx = _Ctx()
    app = FastAPI()
    app.include_router(routes.create_router(ctx))  # type: ignore[arg-type]
    return TestClient(app), ctx, republished


def test_the_guild_messages_are_listed_with_their_link(setup):
    client, _, _ = setup

    response = client.get("/api/servers/1/bot-messages")

    assert response.status_code == 200
    assert response.json() == {
        "messages": [
            {
                "slot": "satisfactory",
                "label": "Statut Satisfactory",
                "channelId": "10",
                "messageId": "100",
                "url": "https://discord.com/channels/1/10/100",
            }
        ]
    }


def test_another_guild_is_refused(setup):
    client, _, republished = setup

    assert client.get("/api/servers/2/bot-messages").status_code == 403
    response = client.post("/api/servers/2/bot-messages/republish", json={"slot": "satisfactory"})
    assert response.status_code == 403
    assert republished == []


def test_a_database_failure_stays_generic(setup):
    client, _, _ = setup
    _FakeRepo.failing = True

    response = client.get("/api/servers/1/bot-messages")

    assert response.status_code == 500
    assert "mongo" not in response.text


def test_republishing_runs_on_the_bot_loop(setup):
    client, ctx, republished = setup

    response = client.post("/api/servers/1/bot-messages/republish", json={"slot": "satisfactory"})

    assert response.status_code == 200
    assert republished == [("1", "satisfactory")]
    assert len(ctx.calls) == 1  # dispatched through call_bot, never inline


def test_republishing_an_unknown_message_is_a_404(setup):
    client, _, _ = setup

    response = client.post("/api/servers/1/bot-messages/republish", json={"slot": "nope"})

    assert response.status_code == 404


def test_a_republish_needs_a_slot(setup):
    client, _, republished = setup

    assert client.post("/api/servers/1/bot-messages/republish", json={}).status_code == 422
    assert republished == []
