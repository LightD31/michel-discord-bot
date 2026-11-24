"""The server page's overview and checklist against a real MongoDB."""

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from webui_fakes import FakeWebUI

import src.webui.routes.overview as overview_routes
from features.xp import XpRepository
from src.core import clock
from src.webui.audit import AuditRepository, record

pytestmark = pytest.mark.mongo

ADMIN = SimpleNamespace(username="alice", user_id="7")


async def test_each_modules_last_change_and_whether_an_action_happened(mongo):
    gid = mongo.guild_id
    await record(gid, ADMIN, "module.save", target="moduleXp")
    await record(gid, ADMIN, "module.toggle", target="moduleFeur")
    await record(gid, ADMIN, "template.test", target="moduleWelcome")
    await record(gid, ADMIN, "module.save", target="moduleXp")
    repo = AuditRepository(gid)
    entries = await repo.list()
    changes = await repo.last_changes(("module.save", "module.toggle"))
    assert set(changes) == {"moduleXp", "moduleFeur"}
    newest_xp = max(e["at"] for e in entries if e["target"] == "moduleXp")
    assert changes["moduleXp"] == newest_xp
    assert await repo.has("template.test", "moduleWelcome")
    assert not await repo.has("template.test", "moduleBirthday")
    assert not await repo.has("onboarding.announce")


async def test_the_weeks_activity_counts_members_and_messages(mongo):
    repo = XpRepository(mongo.guild_id)
    now = clock.now()
    await asyncio.gather(
        repo.log_event("1", 10, 10, now - timedelta(days=1)),
        repo.log_event("1", 10, 20, now - timedelta(days=2)),
        repo.log_event("2", 5, 5, now - timedelta(hours=3), source="voice"),
        repo.log_event("3", 10, 10, now - timedelta(days=9)),  # before the week
    )
    activity = await overview_routes.week_activity(mongo.guild_id)
    assert activity == {"days": 7, "activeMembers": 2, "messages": 2}


async def test_the_overview_route(mongo):
    gid = mongo.guild_id
    await record(gid, ADMIN, "module.save", target="moduleXp")
    ctx = FakeWebUI.with_server(gid, {"moduleXp": {"enabled": False}}, session=ADMIN)
    router = overview_routes.create_router(ctx)
    get = next(r.endpoint for r in router.routes if r.path.endswith("/overview"))
    data = json.loads((await get(None, gid)).body)
    assert data["activity"] is None  # XP off: no activity to show
    assert [e["target"] for e in data["recent"]] == ["moduleXp"]
    assert set(data["lastChanges"]) == {"moduleXp"}
    ctx.data["servers"][gid] = {"moduleXp": {"enabled": True}}
    data = json.loads((await get(None, gid)).body)
    assert data["activity"] == {"days": 7, "activeMembers": 0, "messages": 0}
