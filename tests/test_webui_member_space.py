"""The member space (C6) without a database: the server list, the gates, the outages.

The routes' data, read and written for the session's member alone, is checked
against a real MongoDB in ``tests/test_mongo_member_space.py``.
"""

import asyncio
import inspect
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import BaseModel

import src.webui.routes.me as me_routes

ME = "42"


class FakeCtx:
    def __init__(self, bot=None, member_of=("1",), guilds=None):
        self.bot = bot
        self.member_of = set(member_of)
        self.session = SimpleNamespace(
            user_id=ME,
            username="moi",
            guilds=guilds
            if guilds is not None
            else [{"id": "1", "name": "Les Copains"}, {"id": "2", "name": "Ailleurs"}],
        )
        self.bot_calls = []

    def require_session(self, request):  # noqa: ARG002
        return self.session

    async def user_is_member(self, session, guild_id):  # noqa: ARG002
        return guild_id in self.member_of

    async def require_guild_member(self, request, guild_id):  # noqa: ARG002
        if guild_id not in self.member_of:
            raise HTTPException(status_code=403, detail="Tu n'es pas membre de ce serveur.")
        return self.session

    async def call_bot(self, func, *args, what: str, **kwargs):  # noqa: ARG002
        self.bot_calls.append(args)
        return {str(i): f"nom-{i}" for i in args[2]}


def endpoint(ctx, method: str, path: str):
    router = me_routes.create_router(ctx)
    return next(r.endpoint for r in router.routes if r.path == path and method in r.methods)


def test_the_server_list_holds_the_servers_i_am_a_member_of(store_config):
    store_config({"servers": {"1": {"moduleXp": {"enabled": True}, "moduleBirthday": {}}}})
    bot = SimpleNamespace(
        guilds=[SimpleNamespace(id=1), SimpleNamespace(id=2), SimpleNamespace(id=3)]
    )
    ctx = FakeCtx(bot=bot, guilds=[{"id": "1", "name": "Les Copains"}, {"id": "2"}, {"id": "9"}])
    out = json.loads(asyncio.run(endpoint(ctx, "GET", "/api/me/servers")(None)).body)
    # 2: no longer a member; 9: Michel left it; 3: I'm not in it.
    assert out == {
        "servers": [{"id": "1", "name": "Les Copains", "icon": None, "sections": ["xp"]}]
    }


def test_a_server_i_am_not_a_member_of_is_refused():
    ctx = FakeCtx()
    for method, path, args in (
        ("GET", "/api/me/servers/{server_id}", ("2",)),
        ("DELETE", "/api/me/servers/{server_id}/birthday", ("2",)),
        ("PUT", "/api/me/servers/{server_id}/santa/wishlist", ("2", me_routes.WishlistBody())),
    ):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(endpoint(ctx, method, path)(None, *args))
        assert exc.value.status_code == 403


def test_every_route_takes_the_member_from_the_session():
    """No path parameter or body field names a member: whose data it is comes from the session."""
    router = me_routes.create_router(FakeCtx())
    for route in router.routes:
        source = inspect.getsource(route.endpoint)
        assert "require_guild_member(" in source or "require_session(" in source, route.path
        assert "user" not in route.path and "member" not in route.path
        for param in inspect.signature(route.endpoint).parameters.values():
            if isinstance(param.annotation, type) and issubclass(param.annotation, BaseModel):
                assert not {"user", "user_id", "member", "author"} & set(
                    param.annotation.model_fields
                )


def test_when_reads_the_moment_back():
    when = endpoint(FakeCtx(), "GET", "/api/me/when")
    found = json.loads(asyncio.run(when(None, q="dans 2h")).body)
    assert found["reading"] and found["at"]
    unread = json.loads(asyncio.run(when(None, q="un jour peut-être")).body)
    assert "Exemple" in unread["error"]


def test_an_unreadable_section_is_named_not_fatal(store_config, monkeypatch):
    store_config(
        {"servers": {"1": {"moduleXp": {"enabled": True}, "moduleUtils": {"enabled": True}}}}
    )

    async def reminders(gid, uid):  # noqa: ARG001
        return {"limit": 25, "items": []}

    monkeypatch.setattr(me_routes.member_space, "reminders", reminders)
    # No MongoDB in these tests: the XP card can't be read.
    out = json.loads(
        asyncio.run(endpoint(FakeCtx(), "GET", "/api/me/servers/{server_id}")(None, "1")).body
    )
    assert out["unavailable"] == ["xp"] and out["xp"] is None
    assert out["reminders"] == {"limit": 25, "items": []}


def test_who_i_owe_is_named(store_config, monkeypatch):
    store_config({"servers": {"1": {"moduleTricount": {"enabled": True, "tricountCurrency": "$"}}}})

    async def tricount(gid, uid):  # noqa: ARG001
        return [
            {
                "id": "g",
                "name": "Vacances",
                "balance": -500,
                "owes": [{"to": "8", "cents": 500}],
                "owed": [{"from": "9", "cents": 1}],
            }
        ]

    monkeypatch.setattr(me_routes.member_space, "tricount", tricount)
    ctx = FakeCtx(bot=SimpleNamespace(guilds=[]))
    out = json.loads(
        asyncio.run(endpoint(ctx, "GET", "/api/me/servers/{server_id}")(None, "1")).body
    )
    assert out["names"] == {"8": "nom-8", "9": "nom-9"} and out["currency"] == "$"
    assert ctx.bot_calls[0][1:] == ("1", [8, 9])


def test_writes_answer_503_when_the_database_is_down(store_config):
    store_config({"servers": {"1": {"moduleBirthday": {"enabled": True}}}})
    put = endpoint(FakeCtx(), "PUT", "/api/me/servers/{server_id}/birthday")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(put(None, "1", me_routes.BirthdayBody(date="12/03")))
    assert exc.value.status_code == 503


def test_the_deletion_needs_its_confirmation():
    with pytest.raises(ValueError):
        me_routes.DeleteBody(confirm=False)
    with pytest.raises(ValueError):
        me_routes.SettleBody(to="0123", cents=100)
