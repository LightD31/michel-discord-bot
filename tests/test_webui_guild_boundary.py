"""A guild admin's dashboard saves stay inside their guild.

Two checks on ``PUT /api/servers/{id}/modules/{module}``: a channel the save
adds must be one of the guild's (the bot posts there with its own
permissions), and a masked secret can't follow a change of the host it is sent
to (``secret_field(sent_to=…)``).
"""

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import extensions.lldap._common  # noqa: F401 — registers moduleLldap
import extensions.rss._common  # noqa: F401 — registers moduleRss
import extensions.xp._common  # noqa: F401 — registers moduleXp
import src.webui.routes.rolemenus as rolemenu_routes
import src.webui.routes.servers as servers_routes
from src.webui import schemas
from src.webui.secrets import SECRET_PLACEHOLDER

GUILD = "1"
STORED_PASSWORD = "stored-admin-password"  # pragma: allowlist secret


class FakeChannel:
    def __init__(self, cid: int, guild_id: str) -> None:
        self.id = cid
        self._guild_id = guild_id

    async def send(self, *args, **kwargs):  # noqa: ARG002 — never called here
        raise AssertionError("nothing is posted by a save")


class FakeBot:
    """Channels 1x belong to guild 1, 2x to guild 2; anything else is unknown."""

    def __init__(self) -> None:
        self.fetched: list[int] = []

    async def fetch_guild(self, gid):
        return SimpleNamespace(id=int(gid), roles=[])

    async def fetch_channel(self, cid):
        self.fetched.append(int(cid))
        owner = str(cid)[0]
        if len(str(cid)) != 2 or owner not in "12":
            raise LookupError("Unknown Channel")
        return FakeChannel(int(cid), owner)


class FakeCtx:
    def __init__(self, stored: dict | None = None) -> None:
        self.bot = FakeBot()
        self.data = {"config": {}, "servers": {GUILD: stored or {}}}
        self.writes = 0

    async def require_guild_admin(self, request, guild_id):  # noqa: ARG002 — signature parity
        return SimpleNamespace(username="admin", user_id="9")

    async def call_bot(self, func, *args, what: str, **kwargs):  # noqa: ARG002
        return await func(*args, **kwargs)

    def get_full_config(self):
        return copy.deepcopy(self.data)

    def mutate_config(self, mutator):
        data = copy.deepcopy(self.data)
        mutator(data)  # raising here means nothing is written
        self.data = data
        self.writes += 1
        return data

    def bot_loop_alive(self):
        return False

    def module_extension_map(self, *, on_save: bool = False):  # noqa: ARG002
        return {}


def save(ctx: FakeCtx, module: str, config: dict):
    router = servers_routes.create_router(ctx)
    put = next(
        r.endpoint
        for r in router.routes
        if r.path == "/api/servers/{server_id}/modules/{module_name}" and "PUT" in r.methods
    )
    return asyncio.run(put(None, GUILD, module, servers_routes.ConfigUpdate(config=config)))


# ── channels ────────────────────────────────────────────────────────────


def test_a_channel_of_the_guild_is_accepted():
    ctx = FakeCtx()
    resp = save(ctx, "moduleXp", {"enabled": True, "xpChannelId": "11"})
    assert json.loads(resp.body)["status"] == "ok"
    assert ctx.data["servers"][GUILD]["moduleXp"]["xpChannelId"] == "11"


@pytest.mark.parametrize("channel", ["21", "99", "not-an-id"])
def test_another_guilds_or_an_unknown_channel_is_refused(channel):
    ctx = FakeCtx()
    with pytest.raises(HTTPException) as exc:
        save(ctx, "moduleXp", {"enabled": True, "xpChannelId": channel})
    assert exc.value.status_code == 422 and channel in exc.value.detail
    assert ctx.writes == 0


def test_nested_channels_are_checked_too():
    ctx = FakeCtx()
    feeds = {"news": {"url": "https://example.com/rss", "channel_id": "21"}}
    with pytest.raises(HTTPException) as exc:
        save(ctx, "moduleRss", {"enabled": True, "ChannelId": "11", "rssFeeds": feeds})
    assert exc.value.status_code == 422 and "21" in exc.value.detail
    assert ctx.writes == 0


def test_only_channels_the_save_adds_are_checked():
    # A channel deleted since the last save doesn't block an unrelated edit.
    ctx = FakeCtx({"moduleXp": {"enabled": True, "xpChannelId": "99"}})
    save(ctx, "moduleXp", {"enabled": True, "xpChannelId": "99", "xpPinMessage": True})
    assert ctx.bot.fetched == []
    assert ctx.writes == 1


def test_channel_ids_reads_top_level_and_nested_fields():
    rss = schemas.module_model("moduleRss")
    config = {
        "ChannelId": " 11 ",
        "rssFeeds": {"a": {"channel_id": "12"}, "b": {"url": "x"}, "c": "junk"},
    }
    assert schemas.channel_ids(rss, config) == {"11", "12"}
    assert schemas.channel_ids(rss, None) == set()


def test_a_role_menu_is_only_posted_in_the_guilds_own_channels():
    bot = FakeBot()
    guild, channel = asyncio.run(rolemenu_routes._resolve_guild_and_channel(bot, GUILD, "12"))
    assert guild.id == 1 and channel.id == 12

    with pytest.raises(HTTPException) as exc:
        asyncio.run(rolemenu_routes._resolve_guild_and_channel(bot, GUILD, "22"))
    assert exc.value.status_code == 400


# ── secrets ─────────────────────────────────────────────────────────────


def _lldap(url: str, password: str) -> dict:
    return {
        "enabled": True,
        "lldapUrl": url,
        "lldapLdapUrl": "ldaps://lldap.example:6360",
        "lldapBaseDn": "dc=example,dc=com",
        "lldapAdminUsername": "admin",
        "lldapAdminPassword": password,
    }


def test_a_masked_secret_is_kept_while_its_host_stays():
    ctx = FakeCtx({"moduleLldap": _lldap("https://lldap.example", STORED_PASSWORD)})
    edited = {**_lldap("https://lldap.example", SECRET_PLACEHOLDER), "lldapBaseDn": "dc=other"}
    save(ctx, "moduleLldap", edited)
    assert ctx.data["servers"][GUILD]["moduleLldap"]["lldapAdminPassword"] == STORED_PASSWORD


def test_a_masked_secret_does_not_follow_a_new_host():
    ctx = FakeCtx({"moduleLldap": _lldap("https://lldap.example", STORED_PASSWORD)})
    with pytest.raises(HTTPException) as exc:
        save(ctx, "moduleLldap", _lldap("https://attacker.example", SECRET_PLACEHOLDER))
    assert exc.value.status_code == 422 and "Mot de passe administrateur" in exc.value.detail
    assert STORED_PASSWORD not in exc.value.detail
    assert ctx.writes == 0
