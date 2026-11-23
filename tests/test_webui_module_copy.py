"""A module's settings copied from another server (P35, src/webui/module_copy.py)."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import src.webui.routes.module_copy as copy_routes
from src.webui.module_copy import copied_settings

SCHEMA = {
    "fields": {
        "enabled": {"type": "boolean"},
        "threshold": {"type": "number", "label": "Seuil"},
        "logChannelId": {"type": "channel", "label": "Salon des logs"},
        "staffRoleId": {"type": "role", "label": "Rôle du staff"},
        "rewards": {"type": "keyvaluemap", "label": "Récompenses", "roleUse": "grant"},
        "feeds": {"type": "rssfeedmap", "label": "Flux"},
        "apiUrl": {"type": "url", "label": "URL de l'API"},
        "apiKey": {"type": "secret", "label": "Clé", "secret": True, "sentTo": ["apiUrl"]},
    }
}
SOURCE_KEY = "source-key"  # pragma: allowlist secret
TARGET_KEY = "target-key"  # pragma: allowlist secret
SOURCE = {
    "enabled": True,
    "threshold": 7,
    "logChannelId": "111",
    "staffRoleId": "222",
    "rewards": {"5": "333"},
    "feeds": {"news": {"url": "https://example.org/rss", "channel_id": "444"}},
    "apiUrl": "https://api.source.example",
    "apiKey": SOURCE_KEY,
}
TARGET = {"enabled": False, "logChannelId": "999", "apiUrl": "https://api.target.example"}


def test_what_names_a_guild_stays_the_targets_and_is_listed_to_fill():
    out, to_fill = copied_settings(SCHEMA, SOURCE, TARGET, with_secrets=True)
    assert out["threshold"] == 7 and out["enabled"] is False  # the target's switch
    assert out["logChannelId"] == "999"  # the target's own channel
    assert "staffRoleId" not in out and "rewards" not in out
    assert out["feeds"] == {"news": {"url": "https://example.org/rss"}}
    assert out["apiKey"] == SOURCE_KEY and out["apiUrl"] == "https://api.source.example"
    assert to_fill == ["Rôle du staff", "Récompenses", "Flux"]


def test_without_the_right_the_secret_and_where_it_goes_stay_the_targets():
    target = {**TARGET, "apiKey": TARGET_KEY}
    out, to_fill = copied_settings(SCHEMA, SOURCE, target, with_secrets=False)
    # Not the source's host either: the target's key would be sent there.
    assert out["apiKey"] == TARGET_KEY and out["apiUrl"] == "https://api.target.example"
    out, to_fill = copied_settings(SCHEMA, SOURCE, TARGET, with_secrets=False)
    assert "apiKey" not in out and "Clé" in to_fill


class Ctx:
    bot = object()

    def __init__(self, manages=("1", "2"), admin_of=("2",)):
        self.manages, self.admin_of = set(manages), set(admin_of)
        self.session = SimpleNamespace(
            user_id="5",
            username="camille",
            guilds=[{"id": "1", "name": "Ici"}, {"id": "2", "name": "Là-bas"}],
        )
        self.config = {"servers": {"1": {"moduleTest": {}}, "2": {"moduleTest": dict(SOURCE)}}}

    async def require_guild_admin(self, request, guild_id):
        if str(guild_id) not in self.manages:
            raise HTTPException(status_code=403, detail="no")
        return self.session

    async def user_manages_guild(self, session, guild_id):
        return str(guild_id) in self.manages

    async def guild_access(self, session, guild_id):
        return SimpleNamespace(is_owner=False, permissions=8 if guild_id in self.admin_of else 32)

    def get_full_config(self):
        return self.config


@pytest.fixture
def routes(monkeypatch):
    monkeypatch.setattr(
        copy_routes, "module_schema", lambda name: SCHEMA if name == "moduleTest" else None
    )
    saved = []

    async def save(ctx, server_id, module_name, config, session, **kwargs):
        saved.append((server_id, module_name, config, kwargs))
        return {"reload": None}

    monkeypatch.setattr(copy_routes, "save_module_settings", save)

    def endpoint(ctx, method, suffix):
        router = copy_routes.create_router(ctx)
        path = "/api/servers/{server_id}/modules/{module_name}/" + suffix
        return next(r.endpoint for r in router.routes if r.path == path and method in r.methods)

    return SimpleNamespace(endpoint=endpoint, saved=saved)


def test_the_sources_are_the_editors_other_servers_with_settings(routes):
    ctx = Ctx()
    body = asyncio.run(routes.endpoint(ctx, "GET", "copy-sources")(None, "1", "moduleTest")).body
    assert json.loads(body)["sources"] == [
        {"id": "2", "name": "Là-bas", "secrets": True, "hasSecrets": True}
    ]
    ctx = Ctx(admin_of=())  # manages, but no administrator there
    body = asyncio.run(routes.endpoint(ctx, "GET", "copy-sources")(None, "1", "moduleTest")).body
    assert json.loads(body)["sources"][0]["secrets"] is False


def test_a_copy_goes_through_the_save_path_and_says_what_to_fill(routes):
    post = routes.endpoint(Ctx(admin_of=()), "POST", "copy-from")
    out = json.loads(
        asyncio.run(post(None, "1", "moduleTest", copy_routes.CopyBody(source="2"))).body
    )
    ((server, module, config, kwargs),) = routes.saved
    assert (server, module, kwargs["action"]) == ("1", "moduleTest", "module.copy")
    assert kwargs["details"] == {"from": "Là-bas"}
    assert "apiKey" not in config and out["secretsCopied"] is False
    assert "Clé" in out["toFill"]


def test_a_server_the_editor_doesnt_manage_is_no_source(routes):
    post = routes.endpoint(Ctx(manages=("1",)), "POST", "copy-from")
    with pytest.raises(HTTPException) as refused:
        asyncio.run(post(None, "1", "moduleTest", copy_routes.CopyBody(source="2")))
    assert refused.value.status_code == 403 and routes.saved == []
