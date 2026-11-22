"""The global settings' accounts named by id (P17): GET /api/accounts, developers only."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import src.webui.routes.config as config_routes


class Bot:
    users = {
        111111111111111111: SimpleNamespace(
            username="lightd", global_name="LightD", display_name="LightD"
        ),
        222222222222222222: SimpleNamespace(
            username="michel", global_name=None, display_name="michel"
        ),
    }

    def get_user(self, uid):
        return None  # not cached: asked to Discord

    async def fetch_user(self, uid):
        return self.users.get(int(uid))


class Ctx:
    def __init__(self, developer=True, bot=None):
        self.developer = developer
        self.bot = bot
        self.asked = []

    def require_developer(self, request):  # noqa: ARG002
        if not self.developer:
            raise HTTPException(status_code=403, detail="Réservé aux développeurs")

    async def call_bot(self, func, *args, what, **kwargs):  # noqa: ARG002
        self.asked.append(args[1])
        return await func(*args, **kwargs)


def accounts(ctx, ids):
    router = config_routes.create_router(ctx)
    get = next(r.endpoint for r in router.routes if r.path == "/api/accounts")
    return json.loads(asyncio.run(get(None, ids)).body)


def test_each_known_account_is_named_and_the_rest_left_out():
    ctx = Ctx(bot=Bot())
    out = accounts(ctx, "111111111111111111,222222222222222222,333333333333333333,abc")
    assert out == {
        "111111111111111111": {"name": "LightD", "username": "lightd"},
        "222222222222222222": {"name": "michel", "username": "michel"},
    }
    assert ctx.asked == [["111111111111111111", "222222222222222222", "333333333333333333"]]


def test_only_developers_ask_and_never_more_than_25():
    with pytest.raises(HTTPException) as refused:
        accounts(Ctx(developer=False, bot=Bot()), "111111111111111111")
    assert refused.value.status_code == 403
    ctx = Ctx(bot=Bot())
    accounts(ctx, ",".join(str(10**17 + i) for i in range(40)))
    assert len(ctx.asked[0]) == 25
    assert accounts(Ctx(bot=None), "111111111111111111") == {}  # no bot: nothing to name
