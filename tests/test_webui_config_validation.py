"""Dashboard saves are checked against the module/section schemas before being written."""

import asyncio
import json

import pytest
from fastapi import HTTPException
from webui_fakes import FakeWebUI

import extensions.xp._common  # noqa: F401 — registers the moduleXp schema
import src.webui.routes.config as config_routes
import src.webui.routes.servers as servers_routes
from src.webui import schemas

# ── The validator ───────────────────────────────────────────────────────


def xp_errors(value):
    return schemas.validation_errors(schemas.module_model("moduleXp"), value, module=True)


def test_a_valid_module_config_passes():
    assert xp_errors({"enabled": True, "xpChannelId": "123", "levelRewards": {"5": "999"}}) == []


def test_unknown_keys_are_refused():
    (error,) = xp_errors({"enabled": True, "leftover": 1})
    assert error == "leftover : champ inconnu"


def test_wrong_types_are_refused():
    errors = xp_errors({"levelRewards": ["not", "a", "map"], "xpPinMessage": [1]})
    assert {e.split(" : ")[0] for e in errors} == {
        "« Récompenses par niveau »",
        "« Épingler le leaderboard »",
    }


def test_numeric_ids_are_accepted_for_string_fields():
    assert xp_errors({"xpChannelId": 123456789012345678}) == []


def test_message_list_weights_must_be_numbers():
    """The SPA writes each weight back into an input: only a number may reach it."""
    hostile = [{"text": "Bravo {mention}", "weight": '1" autofocus onfocus="alert(1)'}]
    (error,) = xp_errors({"levelUpMessageList": hostile})
    assert error.startswith("« Messages de level-up » › n°1 › weight : ")
    assert xp_errors({"levelUpMessageList": [{"text": "x", "weight": -1}]}) != []
    assert xp_errors({"levelUpMessageList": [{"text": "x", "extra": 1}]}) != []
    assert xp_errors({"levelUpMessageList": [{"text": "x", "weight": "2"}, {"text": "y"}]}) == []


def test_a_number_out_of_its_bounds_is_refused_in_french():
    """The dashboard saved a starboard threshold of -3 with a success toast (Q2)."""
    import extensions.moderation._common  # noqa: F401 — registers moduleModeration
    import extensions.starboard._common  # noqa: F401 — registers moduleStarboard

    starboard = schemas.module_model("moduleStarboard")
    (error,) = schemas.validation_errors(starboard, {"threshold": -3}, module=True)
    assert error == "« Seuil » : doit être supérieur ou égal à 1"
    moderation = schemas.module_model("moduleModeration")
    (error,) = schemas.validation_errors(moderation, {"spamThreshold": 1}, module=True)
    assert error == "« Seuil anti-spam (messages) » : doit être supérieur ou égal à 2"
    (error,) = schemas.validation_errors(moderation, {"spamWindowSeconds": 10**6}, module=True)
    assert " : doit être inférieur ou égal à " in error
    backup = schemas.section_model("backup")
    assert schemas.validation_errors(backup, {"maxBackups": 0}, module=False) != []
    assert schemas.validation_errors(backup, {"backupHour": 24}, module=False) != []
    assert (
        schemas.validation_errors(backup, {"maxBackups": 3, "backupHour": 23}, module=False) == []
    )


def test_enabled_is_always_allowed_on_a_module_but_must_be_a_boolean():
    model = schemas.module_model("discord2name")  # no ``enabled`` field, direct value
    assert schemas.validation_errors(model, {"1": "Alice"}, module=True) == []
    assert schemas.validation_errors(model, {"1": ["Alice"]}, module=True) != []


def test_a_section_must_be_an_object():
    model = schemas.section_model("discord")
    assert schemas.validation_errors(model, {"devGuildId": "1"}, module=False) == []
    assert schemas.validation_errors(model, ["x"], module=False) != []
    assert schemas.validation_errors(model, {"enabled": True}, module=False) != []


def test_config_problems_reports_every_location():
    data = {
        "config": {"discord": {"devGuildId": "1", "typo": True}, "unregistered": {"x": 1}},
        "servers": {
            "42": {
                "moduleXp": {"enabled": "maybe"},
                "publicDashboardToken": "not a module",
            }
        },
    }
    problems = schemas.config_problems(data)
    assert [p.split(":")[0] for p in problems] == ["config.discord", "servers.42.moduleXp"]


def test_config_problems_is_empty_for_a_valid_config():
    data = {
        "config": {"discord": {"devGuildId": "1"}},
        "servers": {"42": {"moduleXp": {"enabled": True, "xpChannelId": "123"}}},
    }
    assert schemas.config_problems(data) == []


# ── The save routes ─────────────────────────────────────────────────────


def fake_ctx():
    return FakeWebUI(developer=True)


def endpoint(module, ctx, method, path):
    router = module.create_router(ctx)
    return next(r for r in router.routes if r.path == path and method in r.methods).endpoint


def test_an_invalid_module_save_is_refused_and_nothing_is_written():
    ctx = fake_ctx()
    put = endpoint(servers_routes, ctx, "PUT", "/api/servers/{server_id}/modules/{module_name}")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            put(None, "1", "moduleXp", servers_routes.ConfigUpdate(config={"xpPinMessage": [1]}))
        )
    assert exc.value.status_code == 422 and "« Épingler le leaderboard »" in exc.value.detail
    assert ctx.written == []

    resp = asyncio.run(
        put(None, "1", "moduleXp", servers_routes.ConfigUpdate(config={"enabled": True}))
    )
    assert json.loads(resp.body)["status"] == "ok"
    assert ctx.written[-1]["servers"]["1"]["moduleXp"] == {"enabled": True}


def test_global_sections_are_validated_and_unknown_ones_refused():
    ctx = fake_ctx()
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


def test_a_refused_save_names_the_field_as_the_form_shows_it():
    """P15 (set aside in P1): « Seuil » rather than ``threshold``, in French."""
    import extensions.starboard._common  # noqa: F401 — registers moduleStarboard
    import extensions.twitch._common  # noqa: F401 — registers moduleTwitch

    starboard = schemas.module_model("moduleStarboard")
    (error,) = schemas.validation_errors(starboard, {"threshold": -3}, module=True)
    assert error == "« Seuil » : doit être supérieur ou égal à 1"
    (error,) = schemas.validation_errors(starboard, {"threshold": "beaucoup"}, module=True)
    assert error == "« Seuil » : doit être un nombre entier"
    twitch = schemas.module_model("moduleTwitch")
    entry = {"twitchNotificationTemplate": "x" * 600}
    (error,) = schemas.validation_errors(
        twitch, {"twitchStreamerList": {"zerator": entry}}, module=True
    )
    assert error.startswith("« Streamers suivis » › zerator › twitchNotificationTemplate : ")
    assert "500 caractères" in error
    (error,) = schemas.validation_errors(starboard, {"oups": 1}, module=True)
    assert error == "oups : champ inconnu"


def test_saving_a_global_section_reloads_what_took_it_at_load(monkeypatch):
    """P34: Zevent took its section at load, and "reload it after a change" led nowhere."""
    calls: list = []

    async def reload(ctx, paths, why):
        calls.append(("reload", paths))
        return {"reloaded": ", ".join(paths), "error": None, "skipped": False}

    async def sync(ctx):
        calls.append(("sync",))

    monkeypatch.setattr(config_routes, "reload_extensions", reload)
    monkeypatch.setattr(config_routes, "sync_commands", sync)
    ctx = fake_ctx()
    ctx.bot = object()
    ctx.section_extensions = lambda section: ["extensions.backup"] if section == "discord" else []
    put = endpoint(config_routes, ctx, "PUT", "/api/global-config/{section}")

    def save(section, value):
        body = config_routes.GlobalConfigUpdate(section=section, config=value)
        return json.loads(asyncio.run(put(None, section, body)).body)

    assert save("discord", {"devGuildId": "1"})["reload"]["reloaded"] == "extensions.backup"
    assert calls == [("reload", ["extensions.backup"]), ("sync",)]
    calls.clear()
    assert "reload" not in save("mongodb", {"url": "mongodb://x"})  # nothing took it
    assert calls == []
