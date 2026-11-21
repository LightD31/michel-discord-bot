"""A module's earlier settings, and putting one back, against a real MongoDB (C5)."""

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import extensions.lldap._common  # noqa: F401 — registers moduleLldap (a secret bound to URLs)
import extensions.xp._common  # noqa: F401 — registers moduleXp
import src.webui.routes.config_history as history_routes
import src.webui.routes.servers as servers_routes
from src.webui import audit
from src.webui.config_history import RETENTION, ConfigHistoryRepository
from src.webui.secrets import SECRET_PLACEHOLDER

pytestmark = pytest.mark.mongo

ADMIN = SimpleNamespace(username="alice", user_id="7")
LLDAP = {
    "enabled": True,
    "lldapUrl": "https://lldap.example.com",
    "lldapLdapUrl": "ldaps://lldap.example.com:6360",
    "lldapBaseDn": "dc=example,dc=com",
    "lldapAdminUsername": "admin",
    "lldapAdminPassword": "hunter2",  # pragma: allowlist secret
}


class FakeCtx:
    bot = None

    def __init__(self, gid, server):
        self.gid = gid
        self.data = {"config": {}, "servers": {gid: server}}

    async def require_guild_admin(self, request, guild_id):  # noqa: ARG002
        return ADMIN

    def mutate_config(self, mutator):
        mutator(self.data)
        return self.data

    def bot_loop_alive(self):
        return False

    def get_full_config(self):
        return self.data


def endpoint(module, ctx, method, path):
    router = module.create_router(ctx)
    return next(r for r in router.routes if r.path == path and method in r.methods).endpoint


@pytest.fixture
def actions(monkeypatch):
    seen = []

    async def record(guild_id, session, action, **kwargs):  # noqa: ARG001
        seen.append((action, kwargs))

    monkeypatch.setattr(audit, "record", record)
    return seen


async def save(ctx, module, config):
    put = endpoint(servers_routes, ctx, "PUT", "/api/servers/{server_id}/modules/{module_name}")
    await put(None, ctx.gid, module, servers_routes.ConfigUpdate(config=config))


async def history(ctx, module):
    get = endpoint(
        history_routes, ctx, "GET", "/api/servers/{server_id}/modules/{module_name}/history"
    )
    return json.loads((await get(None, ctx.gid, module)).body)["versions"]


async def restore(ctx, module, version_id):
    post = endpoint(
        history_routes,
        ctx,
        "POST",
        "/api/servers/{server_id}/modules/{module_name}/history/{version_id}/restore",
    )
    return json.loads((await post(None, ctx.gid, module, version_id)).body)


async def test_each_save_keeps_the_settings_it_replaced(mongo, actions):  # noqa: ARG001
    ctx = FakeCtx(mongo.guild_id, {"moduleXp": {"enabled": True, "xpChannelId": "111"}})
    await save(ctx, "moduleXp", {"enabled": True, "xpChannelId": "222"})
    await save(ctx, "moduleXp", {"enabled": True, "xpChannelId": "222"})  # no change: no version
    await save(ctx, "moduleXp", {"enabled": False, "xpChannelId": "222"})
    versions = await history(ctx, "moduleXp")
    assert [v["settings"] for v in versions] == [
        {"enabled": True, "xpChannelId": "222"},
        {"enabled": True, "xpChannelId": "111"},
    ]
    # What the save after each version changed.
    assert [v["changed"] for v in versions] == [["enabled"], ["xpChannelId"]]
    indexes = await mongo.guild_collection("config_history").index_information()
    assert indexes["at_ttl"]["expireAfterSeconds"] == int(RETENTION.total_seconds())


async def test_a_restore_is_a_save_that_keeps_todays_secrets(mongo, actions):
    ctx = FakeCtx(mongo.guild_id, {"moduleLldap": dict(LLDAP)})
    await save(
        ctx,
        "moduleLldap",
        {**LLDAP, "lldapAdminPassword": SECRET_PLACEHOLDER, "lldapBaseDn": "dc=new"},
    )
    (version,) = await history(ctx, "moduleLldap")
    assert "lldapAdminPassword" not in version["settings"]  # a secret is never copied
    stored = await ConfigHistoryRepository(mongo.guild_id).list("moduleLldap")
    assert "hunter2" not in json.dumps(stored, default=str)

    await restore(ctx, "moduleLldap", version["id"])
    current = ctx.data["servers"][mongo.guild_id]["moduleLldap"]
    assert current["lldapBaseDn"] == "dc=example,dc=com"
    assert current["lldapAdminPassword"] == "hunter2"  # pragma: allowlist secret
    assert actions[-1][0] == "module.restore"
    assert actions[-1][1]["changes"] == ["lldapBaseDn"]
    # The restore replaced settings too: they are a version in turn.
    assert len(await history(ctx, "moduleLldap")) == 2


async def test_an_old_url_never_gets_todays_password(mongo, actions):  # noqa: ARG001
    ctx = FakeCtx(mongo.guild_id, {"moduleLldap": dict(LLDAP)})
    moved = dict(LLDAP, lldapUrl="https://new.example.com")
    moved["lldapAdminPassword"] = "s3cret"  # pragma: allowlist secret
    await save(ctx, "moduleLldap", moved)
    (version,) = await history(ctx, "moduleLldap")
    with pytest.raises(HTTPException) as refused:
        await restore(ctx, "moduleLldap", version["id"])
    assert refused.value.status_code == 422
    current = ctx.data["servers"][mongo.guild_id]["moduleLldap"]
    assert current["lldapUrl"] == "https://new.example.com"  # nothing written


async def test_a_restore_is_checked_like_a_save(mongo, actions):  # noqa: ARG001
    ctx = FakeCtx(mongo.guild_id, {"moduleXp": {"enabled": True, "xpChannelId": "111"}})
    await save(ctx, "moduleXp", {"enabled": True, "xpChannelId": "222"})
    (version,) = await history(ctx, "moduleXp")
    # A hand edit put an invalid value in the stored version.
    await mongo.guild_collection("config_history").update_one(
        {}, {"$set": {"settings.xpChannelId": ["not", "an", "id"]}}
    )
    with pytest.raises(HTTPException) as refused:
        await restore(ctx, "moduleXp", version["id"])
    assert refused.value.status_code == 422
    assert ctx.data["servers"][mongo.guild_id]["moduleXp"]["xpChannelId"] == "222"


@pytest.mark.parametrize("version_id", ["ab" * 12, "nope"])
async def test_an_unknown_version_is_a_404(mongo, actions, version_id):  # noqa: ARG001
    ctx = FakeCtx(mongo.guild_id, {"moduleXp": {"enabled": True}})
    with pytest.raises(HTTPException) as missing:
        await restore(ctx, "moduleXp", version_id)
    assert missing.value.status_code == 404


async def test_a_version_of_another_module_is_not_found(mongo, actions):  # noqa: ARG001
    ctx = FakeCtx(mongo.guild_id, {"moduleXp": {"enabled": True, "xpChannelId": "111"}})
    await save(ctx, "moduleXp", {"enabled": True, "xpChannelId": "222"})
    (version,) = await history(ctx, "moduleXp")
    with pytest.raises(HTTPException) as missing:
        await restore(ctx, "moduleLldap", version["id"])
    assert missing.value.status_code == 404
