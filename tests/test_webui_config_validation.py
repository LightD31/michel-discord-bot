"""Dashboard saves are checked against the module/section schemas before being written."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import extensions.xp._common  # noqa: F401 — registers the moduleXp schema
import src.webui.routes.config as config_routes
import src.webui.routes.servers as servers_routes
from scripts.check_config import check
from src.webui import schemas

# ── The validator ───────────────────────────────────────────────────────


def xp_errors(value):
    return schemas.validation_errors(schemas.module_model("moduleXp"), value, module=True)


def test_a_valid_module_config_passes():
    assert xp_errors({"enabled": True, "xpChannelId": "123", "levelRewards": {"5": "999"}}) == []


def test_unknown_keys_are_refused():
    (error,) = xp_errors({"enabled": True, "leftover": 1})
    assert error.startswith("leftover:")


def test_wrong_types_are_refused():
    errors = xp_errors({"levelRewards": ["not", "a", "map"], "xpPinMessage": [1]})
    assert {e.split(":")[0] for e in errors} == {"levelRewards", "xpPinMessage"}


def test_numeric_ids_are_accepted_for_string_fields():
    assert xp_errors({"xpChannelId": 123456789012345678}) == []


def test_enabled_is_always_allowed_on_a_module_but_must_be_a_boolean():
    model = schemas.module_model("discord2name")  # no ``enabled`` field, direct value
    assert schemas.validation_errors(model, {"1": "Alice"}, module=True) == []
    assert schemas.validation_errors(model, {"1": ["Alice"]}, module=True) != []


def test_a_section_must_be_an_object():
    model = schemas.section_model("discord")
    assert schemas.validation_errors(model, {"devGuildId": "1"}, module=False) == []
    assert schemas.validation_errors(model, ["x"], module=False) != []
    assert schemas.validation_errors(model, {"enabled": True}, module=False) != []


def test_check_config_reports_every_location():
    data = {
        "config": {"discord": {"devGuildId": "1", "typo": True}, "unregistered": {"x": 1}},
        "servers": {
            "42": {
                "moduleXp": {"enabled": "maybe"},
                "publicDashboardToken": "not a module",
            }
        },
    }
    problems = check(data)
    assert [p.split(":")[0] for p in problems] == ["config.discord", "servers.42.moduleXp"]


# ── The save routes ─────────────────────────────────────────────────────


class FakeCtx:
    bot = None

    def __init__(self):
        self.written: list[dict] = []

    async def require_guild_admin(self, request, guild_id):  # noqa: ARG002 — signature parity
        return SimpleNamespace(username="admin", user_id="1")

    def require_developer(self, request):  # noqa: ARG002 — signature parity
        return SimpleNamespace(username="dev", user_id="2")

    def mutate_config(self, mutator):
        data = {"config": {}, "servers": {}}
        mutator(data)  # raising here means nothing is written
        self.written.append(data)
        return data

    def bot_loop_alive(self):
        return False

    def get_full_config(self):
        return {"config": {}, "servers": {}}


def endpoint(module, ctx, method, path):
    router = module.create_router(ctx)
    return next(r for r in router.routes if r.path == path and method in r.methods).endpoint


def test_an_invalid_module_save_is_refused_and_nothing_is_written():
    ctx = FakeCtx()
    put = endpoint(servers_routes, ctx, "PUT", "/api/servers/{server_id}/modules/{module_name}")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            put(None, "1", "moduleXp", servers_routes.ConfigUpdate(config={"xpPinMessage": [1]}))
        )
    assert exc.value.status_code == 422 and "xpPinMessage" in exc.value.detail
    assert ctx.written == []

    resp = asyncio.run(
        put(None, "1", "moduleXp", servers_routes.ConfigUpdate(config={"enabled": True}))
    )
    assert json.loads(resp.body)["status"] == "ok"
    assert ctx.written[-1]["servers"]["1"]["moduleXp"] == {"enabled": True}


def test_global_sections_are_validated_and_unknown_ones_refused():
    ctx = FakeCtx()
    put = endpoint(config_routes, ctx, "PUT", "/api/global-config/{section}")

    def save(section, value):
        body = config_routes.GlobalConfigUpdate(section=section, config=value)
        return asyncio.run(put(None, section, body))

    with pytest.raises(HTTPException) as exc:
        save("notASection", {"x": 1})
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        save("discord", {"devGuildId": "1", "typo": True})
    assert exc.value.status_code == 422 and "typo" in exc.value.detail
    assert ctx.written == []

    assert json.loads(save("discord", {"devGuildId": "1"}).body)["status"] == "ok"
    assert ctx.written[-1]["config"]["discord"] == {"devGuildId": "1"}
