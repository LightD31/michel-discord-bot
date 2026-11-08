"""Phase 5b cleanups: the shared staff check, and the RSS presets' base URL from config."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from interactions import Permissions

import src.webui.routes.config as config_routes
from src.discord_ext.staff import is_staff, top_role_position


class Member:
    def __init__(self, *roles, admin: bool = False) -> None:
        self.roles = [SimpleNamespace(id=r, position=p) for r, p in roles]
        self.admin = admin

    def has_permission(self, permission) -> bool:
        return self.admin and permission == Permissions.ADMINISTRATOR


def test_administrators_are_staff_without_a_staff_role():
    assert is_staff(Member(admin=True), None)
    assert not is_staff(Member((5, 1)), None)


def test_the_staff_role_makes_staff():
    member = Member((5, 1), (7, 3))
    assert is_staff(member, "7") and is_staff(member, 7)
    assert not is_staff(member, "8")


def test_a_malformed_staff_role_or_a_user_is_never_staff():
    assert not is_staff(Member((5, 1)), "not-an-id")
    assert not is_staff(SimpleNamespace(id=1), "5")  # a User: no roles, no permissions


def test_the_top_role_position():
    assert top_role_position(Member((5, 1), (7, 3))) == 3
    assert top_role_position(SimpleNamespace()) == 0


# ── /api/presets/rss ─────────────────────────────────────────────────────


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
