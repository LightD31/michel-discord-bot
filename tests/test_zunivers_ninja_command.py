"""``/zunivers ninja`` echoes the API's error text without letting it ping anyone."""

from discord_fakes import FakeCtx, FakeUser, run

import extensions.zunivers.ninja as ninja_module
from extensions.zunivers.ninja import NinjaMixin
from features.zunivers_ninja import NinjaError
from src.discord_ext.mentions import NO_PINGS


class FailingClient:
    def __init__(self, url):
        self.url = url

    async def get_plan(self, *args, **kwargs):
        raise NinjaError("@everyone " + "x" * 3000)


async def test_an_api_error_is_echoed_clipped_and_without_pings(monkeypatch, store_config):
    store_config({"config": {"zuniversNinja": {"apiUrl": "https://ninja.example"}}})
    monkeypatch.setattr(ninja_module, "NinjaClient", FailingClient)
    ctx = FakeCtx(author=FakeUser(5, "bob"))
    await run(NinjaMixin.ninja_command, NinjaMixin(), ctx)
    (reply,) = ctx.replies
    assert reply["allowed_mentions"] is NO_PINGS
    assert len(reply["content"]) <= 2000
