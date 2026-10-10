"""Dashboard endpoints behind the AI model picker (``src.webui.routes.ai``)."""

from dataclasses import dataclass

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import src.webui.routes.ai as ai_routes
from src.core.config import config_store
from src.integrations import llm
from src.integrations.llm import CatalogModel


@dataclass
class _Session:
    username: str = "dev"
    user_id: str = "1"


class _Ctx:
    def __init__(self):
        self.developer = True
        self.guild_admin = True

    def require_developer(self, request):  # noqa: ARG002 — signature parity
        if not self.developer:
            raise HTTPException(status_code=403, detail="Accès réservé au développeur")
        return _Session()

    def require_guild_admin(self, request, guild_id):  # noqa: ARG002
        if not self.guild_admin:
            raise HTTPException(status_code=403, detail="Accès refusé")
        return _Session()


@pytest.fixture
def setup():
    saved = config_store._data
    data = {
        "config": {
            "AI": {
                "models": [
                    {"provider": "gpt", "model_id": "openai/gpt-4.1", "display_name": "GPT"},
                    {"provider": "nano", "api": "nanogpt", "model_id": "x/y", "display_name": "Y"},
                ],
                "defaultModel": "nano",
            },
            "OpenRouter": {
                "openrouterApiKey": "secret-key",  # pragma: allowlist secret
                "baseUrl": "https://r.example/v1",
            },
        },
        "servers": {},
    }
    config_store._data = data
    ctx = _Ctx()
    app = FastAPI()
    app.include_router(ai_routes.create_router(ctx))
    yield TestClient(app), ctx, data
    config_store._data = saved


def test_catalog_is_developer_only(setup):
    client, ctx, _ = setup
    ctx.developer = False
    assert client.get("/api/ai/catalog", params={"api": "openrouter"}).status_code == 403
    assert client.get("/api/ai/apis").status_code == 403


def test_catalog_rejects_unknown_or_unconfigured_api(setup):
    client, _, _ = setup
    assert client.get("/api/ai/catalog", params={"api": "ollama"}).status_code == 400
    res = client.get("/api/ai/catalog", params={"api": "nanogpt"})
    assert res.status_code == 400
    assert "non configurée" in res.json()["detail"]


def test_catalog_lists_sorted_models(setup, monkeypatch):
    client, _, _ = setup

    async def fake_catalog(name):
        return [
            CatalogModel("z/z", "zeta", name, 1.0, 2.0, 8000),
            CatalogModel("a/a", "Alpha", name, 0, 0, None),
        ]

    monkeypatch.setattr(llm, "fetch_catalog", fake_catalog)
    res = client.get("/api/ai/catalog", params={"api": "openrouter"})
    assert res.status_code == 200
    body = res.json()
    assert [m["id"] for m in body["models"]] == ["a/a", "z/z"]
    assert body["models"][0]["free"] is True


def test_catalog_upstream_failure_is_a_502(setup, monkeypatch):
    client, _, _ = setup

    async def failing(name):
        raise llm.LlmError("down: https://r.example?key=secret-key")

    monkeypatch.setattr(llm, "fetch_catalog", failing)
    res = client.get("/api/ai/catalog", params={"api": "openrouter"})
    assert res.status_code == 502
    assert "secret-key" not in res.text


def test_apis_report_configuration(setup):
    client, _, _ = setup
    assert client.get("/api/ai/apis").json()["apis"] == [
        {"id": "openrouter", "label": "OpenRouter", "configured": True},
        {"id": "nanogpt", "label": "NanoGPT", "configured": False},
    ]


def test_guild_models_expose_only_keys_and_names(setup):
    client, ctx, _ = setup
    res = client.get("/api/servers/123/ai/models")
    assert res.status_code == 200
    assert res.json() == {
        "models": [
            {"key": "gpt", "display_name": "GPT"},
            {"key": "nano", "display_name": "Y"},
        ],
        "default": "nano",
    }
    assert "secret-key" not in res.text and "openai/gpt-4.1" not in res.text
    ctx.guild_admin = False
    assert client.get("/api/servers/123/ai/models").status_code == 403
