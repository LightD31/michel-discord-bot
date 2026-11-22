"""The dashboard's Secret Santa card: a summary that keeps the secret, and the draw (P2)."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from discord_fakes import FakeBot, FakeGuild, FakeMember
from fastapi import HTTPException
from santa_fakes import MemorySanta

import src.discord_ext.secretsanta as draw_module
import src.webui.routes.secretsanta as santa_routes
from features.secretsanta import SecretSantaSession
from src.core.permissions import ADMINISTRATOR, MANAGE_GUILD
from src.webui.guild_access import GuildAccess

GUILD = "1"


class Ctx:
    def __init__(self, bot, user_id: str, permissions: int):
        self.bot = bot
        self.user_id = user_id
        self.permissions = permissions

    async def require_guild_admin(self, request, guild_id):  # noqa: ARG002
        return SimpleNamespace(username="alice", user_id=self.user_id, guilds=[])

    def require_bot_loop(self):
        return None

    async def guild_access(self, session, guild_id):  # noqa: ARG002
        return GuildAccess(is_member=True, permissions=self.permissions)

    async def call_bot(self, func, *args, what: str, timeout: float = 30, **kwargs):  # noqa: ARG002
        return await func(*args, **kwargs)


@pytest.fixture
def santa(monkeypatch, tmp_path, store_config):
    store_config(
        {"servers": {GUILD: {"moduleSecretSanta": {"enabled": True}, "discord2name": {"5": "Zoé"}}}}
    )
    monkeypatch.setattr(draw_module, "DATA_DIR", tmp_path)
    guild = FakeGuild(1, "Les Lutins")
    members = {uid: guild.add_member(FakeMember(uid, f"m{uid}")) for uid in (3, 4, 5, 7)}
    bot = FakeBot(guild)
    repo = MemorySanta(
        SecretSantaSession(
            context_id="guild_1",
            channel_id=10,
            created_by=3,
            participants=[3, 4, 5],
            budget="20 €",
            exchange_date="2026-12-24",
            shipping=True,
        )
    )
    monkeypatch.setattr(santa_routes, "SecretSantaRepository", lambda: repo)
    audits = []

    async def record(guild_id, session, action, **kwargs):
        audits.append((guild_id, action, kwargs))

    monkeypatch.setattr(santa_routes.audit, "record", record)

    def call(method, user_id="3", permissions=MANAGE_GUILD, body=None, path="draw"):
        router = santa_routes.create_router(Ctx(bot, user_id, permissions))
        endpoint = next(
            r.endpoint
            for r in router.routes
            if method in r.methods and (method == "GET" or r.path.endswith("/" + path))
        )
        args = (None, GUILD) if method == "GET" or path != "draw" else (None, GUILD, body)
        response = asyncio.run(endpoint(*args))
        return json.loads(response.body)

    return repo, members, call, audits


def test_the_summary_names_participants_and_their_lists(santa):
    repo, _, call, _ = santa
    asyncio.run(repo.set_wishlist("guild_1", 4, "un livre", None))
    data = call("GET")["session"]
    assert data["exchange_label"] == "jeudi 24 décembre 2026" and data["shipping"] is True
    assert data["created_by"] == "m3"
    assert data["participants"] == [
        {"id": "3", "name": "m3", "has_wishlist": False},
        {"id": "4", "name": "m4", "has_wishlist": True},
        {"id": "5", "name": "Zoé", "has_wishlist": False},  # the server's first name
    ]
    assert "sent" not in data  # not drawn yet
    assert "un livre" not in json.dumps(data)  # the list itself stays private


def test_a_drawn_parcel_session_shows_counts_and_never_pairs(santa):
    repo, _, call, _ = santa
    repo.session.is_drawn = True
    repo.results = [(3, 4), (4, 5), (5, 3)]
    asyncio.run(repo.mark_gift("guild_1", 4, "sent_at", None))
    data = call("GET")["session"]
    assert (data["sent"], data["received"], data["total"]) == (1, 0, 3)
    assert "results" not in data and "pairs" not in json.dumps(data)


def test_no_session(santa):
    repo, _, call, _ = santa
    repo.session = None
    assert call("GET") == {"session": None}


def test_the_creator_draws_from_the_dashboard(santa):
    repo, members, call, audits = santa
    result = call("POST", body=santa_routes.DrawRequest())
    assert result == {"count": 3, "subgroups": 1, "failed_dms": []}
    assert repo.session.is_drawn and len(repo.results) == 3
    for uid in (3, 4, 5):
        assert len(members[uid].dm_messages) == 1
    assert audits == [(GUILD, "secretsanta.draw", {"details": {"participants": 3}})]


def test_an_administrator_draws_too(santa):
    repo, _, call, _ = santa
    call("POST", user_id="7", permissions=ADMINISTRATOR, body=santa_routes.DrawRequest())
    assert repo.session.is_drawn


def test_managing_the_server_is_not_enough_to_draw(santa):
    repo, members, call, audits = santa
    with pytest.raises(HTTPException) as exc:
        call("POST", user_id="7", body=santa_routes.DrawRequest())
    assert exc.value.status_code == 403
    assert not repo.session.is_drawn and audits == []
    assert all(m.dm_messages == [] for m in members.values())


def test_a_draw_that_cannot_happen_says_why(santa):
    repo, _, call, audits = santa
    repo.session.is_drawn = True
    with pytest.raises(HTTPException) as exc:
        call("POST", body=santa_routes.DrawRequest())
    assert exc.value.status_code == 409 and "déjà été effectué" in exc.value.detail
    assert audits == []


def test_closed_dms_are_named(santa):
    _, members, call, _ = santa
    members[5].dms_closed = True
    assert call("POST", body=santa_routes.DrawRequest())["failed_dms"] == ["Zoé"]


def test_drawing_without_a_session_is_a_404(santa):
    repo, _, call, _ = santa
    repo.session = None
    with pytest.raises(HTTPException) as exc:
        call("POST", body=santa_routes.DrawRequest())
    assert exc.value.status_code == 404


def test_an_administrator_reveals_the_pairs_and_it_is_audited(santa):
    """P15: as /secretsanta reveler, for the server's administrators only."""
    repo, _, call, audits = santa
    repo.session.is_drawn = True
    repo.results = [(3, 4), (4, 5), (5, 3)]
    assert call("GET", user_id="7", permissions=ADMINISTRATOR)["session"]["can_reveal"] is True
    result = call("POST", user_id="7", permissions=ADMINISTRATOR, path="reveal")
    assert result["pairs"] == [
        {"giver": "m3", "receiver": "m4"},
        {"giver": "m4", "receiver": "Zoé"},
        {"giver": "Zoé", "receiver": "m3"},
    ]
    assert audits == [(GUILD, "secretsanta.reveal", {"details": {"pairs": 3}})]


@pytest.mark.parametrize("user_id", ["3", "7"])  # the creator, a manager of the server
def test_neither_the_creator_nor_a_manager_may_reveal(santa, user_id):
    repo, _, call, audits = santa
    repo.session.is_drawn = True
    repo.results = [(3, 4), (4, 3)]
    assert call("GET", user_id=user_id)["session"]["can_reveal"] is False
    with pytest.raises(HTTPException) as exc:
        call("POST", user_id=user_id, path="reveal")
    assert exc.value.status_code == 403 and audits == []


def test_nothing_to_reveal_before_the_draw(santa):
    _, _, call, audits = santa
    with pytest.raises(HTTPException) as exc:
        call("POST", user_id="7", permissions=ADMINISTRATOR, path="reveal")
    assert exc.value.status_code == 409 and audits == []
