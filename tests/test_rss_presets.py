"""The dashboard's RSS presets: their base URL comes from the ``rss`` section."""

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import src.webui.routes.config as config_routes


class _Ctx:
    def __init__(self, admin: bool = True) -> None:
        self.admin = admin

    def require_admin(self, request):
        if not self.admin:
            raise HTTPException(status_code=403, detail="Accès réservé")


def _client(ctx) -> TestClient:
    app = FastAPI()
    app.include_router(config_routes.create_router(ctx))  # type: ignore[arg-type]
    return TestClient(app)


@pytest.mark.parametrize(
    ("section", "expected"),
    [
        ({"rss": {"lootScraperBaseUrl": "https://feeds.example/"}}, "https://feeds.example/"),
        ({"rss": {}}, ""),
        ({}, ""),
        ({"rss": {"lootScraperBaseUrl": 3}}, ""),
    ],
)
def test_the_presets_base_url_comes_from_the_rss_section(store_config, section, expected):
    store_config({"config": section, "servers": {}})

    response = _client(_Ctx()).get("/api/presets/rss")

    assert response.json() == {"lootScraperBaseUrl": expected}


def test_the_presets_need_a_dashboard_admin(store_config):
    store_config({"config": {}, "servers": {}})
    assert _client(_Ctx(admin=False)).get("/api/presets/rss").status_code == 403
