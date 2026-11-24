"""The Succès module page's card (P25): every badge, in play or why not, and its holders."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from webui_fakes import FakeWebUI

import extensions.achievements  # noqa: F401 — registers moduleAchievements
from src.core.errors import DatabaseError
from src.webui.routes import achievements as routes


def fake_ctx() -> FakeWebUI:
    return FakeWebUI(session=SimpleNamespace(user_id="42"), manages=["1"])


class Repo:
    down = False

    def __init__(self, guild_id):
        pass

    async def holders(self):
        if Repo.down:
            raise DatabaseError("down")
        return {"star": 3, "feur": 1}


def call(server_id="1"):
    router = routes.create_router(fake_ctx())
    endpoint = next(r.endpoint for r in router.routes if r.path.endswith("/achievements"))
    return json.loads(asyncio.run(endpoint(None, server_id)).body)


@pytest.fixture(autouse=True)
def setup(store_config, monkeypatch):
    Repo.down = False
    monkeypatch.setattr(routes, "AchievementsRepository", Repo)
    servers = {
        "1": {
            "moduleAchievements": {"enabled": True, "badgeDj": False},
            "moduleStarboard": {"enabled": True},
        }
    }
    store_config({"servers": servers})
    return servers


def test_each_badge_says_whether_it_is_in_play_and_how_many_won_it():
    badges = {b["key"]: b for b in call()["badges"]}
    assert badges["star"]["off"] is None and badges["star"]["holders"] == 3
    assert badges["dj"]["off"] == "désactivé ici"
    assert badges["feur"]["off"] == "module Feur désactivé" and badges["feur"]["holders"] == 1
    assert badges["voice"]["off"] == "module Système d'XP désactivé"
    assert badges["seniority"]["off"] is None and badges["seniority"]["holders"] == 0


def test_off_module_unreadable_store_and_other_servers_are_refused(setup):
    with pytest.raises(HTTPException) as caught:
        call("2")
    assert caught.value.status_code == 403
    Repo.down = True
    with pytest.raises(DatabaseError):  # the app answers it 503 (src/webui/errors.py)
        call()
    setup["1"]["moduleAchievements"]["enabled"] = False
    with pytest.raises(HTTPException) as caught:
        call()
    assert caught.value.status_code == 404
