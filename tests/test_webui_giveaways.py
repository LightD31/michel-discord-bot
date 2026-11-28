"""The running giveaways on their module page: the list, the draw and the cancellation (P42)."""

from datetime import timedelta

import pytest
from discord_fakes import FakeBot, FakeChannel, FakeGuild, FakeMember, FakeMessage, FakeRole
from fastapi import FastAPI
from fastapi.testclient import TestClient
from giveaway_fakes import MemoryGiveaways
from interactions import Embed
from webui_fakes import FakeWebUI

import src.discord_ext.giveaway as shared
import src.webui.routes.giveaways as giveaway_routes
from features.giveaway import Giveaway
from src.core import clock
from src.webui.errors import install_error_handlers

GUILD = "1"
BASE = f"/api/servers/{GUILD}/giveaways"


@pytest.fixture
def board(monkeypatch, store_config):
    store_config({"servers": {GUILD: {"moduleGiveaway": {"enabled": True}}}})
    guild = FakeGuild(1)
    channel = guild.add_channel(FakeChannel(10, "concours"))
    guild.add_role(FakeRole(20, "Abonnés"))
    guild.add_member(FakeMember(3, "alice"))
    guild.add_member(FakeMember(7, "bob"))
    repo = MemoryGiveaways()
    monkeypatch.setattr(giveaway_routes, "GiveawayRepository", lambda guild_id: repo)

    async def edit(message, **kwargs):
        await message.edit(**kwargs)

    monkeypatch.setattr(shared, "edit_message_if_changed", edit)
    audits = []

    async def record(guild_id, session, action, **kwargs):
        audits.append((guild_id, action, kwargs.get("target")))

    monkeypatch.setattr(giveaway_routes.audit, "record", record)
    message = FakeMessage(500, embeds=[Embed(title="🎁 GIVEAWAY — Un jeu")], channel=channel)
    channel.messages[message.id] = message
    repo.items["a" * 24] = Giveaway(
        _id="a" * 24,
        guild_id=GUILD,
        channel_id="10",
        message_id="500",
        host_id="3",
        prize="Un jeu",
        allow_host_win=False,
        required_role_id="20",
        min_level=5,
        ends_at=clock.now() + timedelta(hours=1),
        entrants=["7"],
    )
    return repo, message, audits, FakeBot(guild)


def client(bot, **kwargs) -> tuple[TestClient, FakeWebUI]:
    ctx = FakeWebUI(bot=bot, **kwargs)
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(giveaway_routes.create_router(ctx))
    return TestClient(app), ctx


def test_the_list_says_what_concours_liste_says(board):
    _, _, _, bot = board
    data = client(bot)[0].get(BASE).json()
    assert data["enabled"] is True
    [g] = data["giveaways"]
    assert g["id"] == "500" and g["prize"] == "Un jeu"
    assert (g["entrants"], g["winners"]) == (1, 1)
    assert (g["host"], g["channel"], g["role"], g["min_level"]) == (
        "alice",
        "concours",
        "Abonnés",
        5,
    )
    assert g["url"].endswith(f"/{GUILD}/10/500")
    assert "bob" not in str(g), "who entered stays on Discord"


def test_draw_now_draws_and_announces(board):
    repo, message, audits, bot = board
    http, _ = client(bot)
    bob = bot.get_guild(1).get_member(7)
    bob.roles = [FakeRole(20, "Abonnés")]
    response = http.post(f"{BASE}/500/draw")
    assert response.status_code == 200, response.text
    assert response.json() == {"prize": "Un jeu", "winners": ["bob"]}
    stored = repo.items["a" * 24]
    assert stored.drawn and stored.winners == ["7"]
    assert message.edits and "<@7>" in message.replies[0]
    assert audits == [(GUILD, "giveaway.draw", "Un jeu")]
    # Drawn: the list no longer has it, and a second draw is refused.
    assert http.get(BASE).json()["giveaways"] == []
    again = http.post(f"{BASE}/500/draw")
    assert again.status_code == 409 and "déjà clos" in again.json()["detail"]


def test_cancel_marks_it_and_its_announcement(board):
    repo, message, audits, bot = board
    http, _ = client(bot)
    response = http.post(f"{BASE}/500/cancel")
    assert response.status_code == 200, response.text
    assert repo.items["a" * 24].cancelled
    assert message.edits and not message.replies
    assert audits == [(GUILD, "giveaway.cancel", "Un jeu")]
    assert http.post(f"{BASE}/500/draw").status_code == 409


def test_with_the_module_off_the_buttons_wait(board, store_config):
    repo, message, audits, bot = board
    store_config({"servers": {GUILD: {"moduleGiveaway": {"enabled": False}}}})
    http, _ = client(bot)
    assert http.get(BASE).json()["enabled"] is False
    for action in ("draw", "cancel"):
        refused = http.post(f"{BASE}/500/{action}")
        assert refused.status_code == 409 and "active le module" in refused.json()["detail"]
    stored = repo.items["a" * 24]
    assert not stored.drawn and not stored.cancelled and not message.edits and audits == []


@pytest.mark.parametrize("action", ["draw", "cancel"])
def test_another_servers_admin_can_do_nothing(board, action):
    repo, message, audits, bot = board
    http, _ = client(bot, manages=set())
    assert http.get(BASE).status_code == 403
    assert http.post(f"{BASE}/500/{action}").status_code == 403
    stored = repo.items["a" * 24]
    assert not stored.drawn and not stored.cancelled and audits == []


@pytest.mark.parametrize("message_id", ["404", "abc", "1" * 21])
def test_an_unknown_giveaway_is_not_found(board, message_id):
    _, _, audits, bot = board
    http, _ = client(bot)
    assert http.post(f"{BASE}/{message_id}/draw").status_code == 404
    assert audits == []
