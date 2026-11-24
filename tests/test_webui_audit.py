"""The dashboard's audit log: what is recorded, and who may read it."""

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from webui_fakes import FakeWebUI

import extensions.xp._common  # noqa: F401 — registers the moduleXp schema
import src.webui.routes.audit as audit_routes
import src.webui.routes.servers as servers_routes
from src.core import clock
from src.core.errors import DatabaseError
from src.webui import audit

ADMIN = SimpleNamespace(username="alice", user_id="7")


def test_changed_fields_lists_names_only():
    before = {"enabled": True, "xpChannelId": "1", "levelRewards": {"5": "9"}}
    after = {"enabled": True, "xpChannelId": "2", "xpPinMessage": True}
    assert audit.changed_fields(before, after) == ["levelRewards", "xpChannelId", "xpPinMessage"]
    assert audit.changed_fields(None, {"a": 1}) == ["a"]
    assert audit.changed_fields({"a": 1}, {"a": 1}) == []


def test_details_are_short_labels():
    clean = audit._clean_details({"n": 3, "ok": True, "label": "x" * 500, "none": None})
    assert clean == {"n": 3, "ok": True, "label": "x" * audit.MAX_DETAIL_LENGTH, "none": None}


def test_an_unreadable_database_never_fails_the_action(monkeypatch, caplog):
    class Down:
        def __init__(self, guild_id):
            pass

        async def add(self, entry):
            raise DatabaseError("down")

    monkeypatch.setattr(audit, "AuditRepository", Down)
    asyncio.run(audit.record("1", ADMIN, "module.save", target="moduleXp"))
    assert any("not recorded" in r.getMessage() for r in caplog.records)


def fake_ctx(stored=None):
    return FakeWebUI.with_server("1", {"moduleXp": stored or {}}, session=ADMIN)


def endpoint(module, ctx, method, path):
    router = module.create_router(ctx)
    return next(r for r in router.routes if r.path == path and method in r.methods).endpoint


@pytest.fixture
def recorded(monkeypatch):
    calls = []

    async def record(guild_id, session, action, **kwargs):
        calls.append((guild_id, session.user_id, action, kwargs))

    monkeypatch.setattr(audit, "record", record)
    return calls


def test_a_module_save_records_the_changed_field_names_not_their_values(recorded):
    ctx = fake_ctx({"enabled": True, "xpChannelId": "111"})
    put = endpoint(servers_routes, ctx, "PUT", "/api/servers/{server_id}/modules/{module_name}")
    body = servers_routes.ConfigUpdate(config={"enabled": True, "xpChannelId": "222"})
    asyncio.run(put(None, "1", "moduleXp", body))
    ((guild_id, user_id, action, kwargs),) = recorded
    assert (guild_id, user_id, action) == ("1", "7", "module.save")
    assert kwargs == {"target": "moduleXp", "changes": ["xpChannelId"]}
    assert "222" not in json.dumps(kwargs)


def test_a_toggle_records_the_new_state(recorded):
    ctx = fake_ctx({"enabled": False})
    post = endpoint(
        servers_routes, ctx, "POST", "/api/servers/{server_id}/modules/{module_name}/toggle"
    )
    asyncio.run(
        post(None, "1", "moduleXp", servers_routes.ModuleToggle(module="moduleXp", enabled=True))
    )
    ((_, _, action, kwargs),) = recorded
    assert action == "module.toggle" and kwargs["details"] == {"enabled": True}


def test_the_bot_log_is_for_developers_only():
    get = endpoint(audit_routes, fake_ctx(), "GET", "/api/audit")
    with pytest.raises(HTTPException) as refused:
        asyncio.run(get(None, limit=10, before=None))
    assert refused.value.status_code == 403


def test_a_bad_before_parameter_is_refused():
    get = endpoint(audit_routes, fake_ctx(), "GET", "/api/servers/{server_id}/audit")
    with pytest.raises(HTTPException) as refused:
        asyncio.run(get(None, "1", limit=10, before="hier"))
    assert refused.value.status_code == 422


@pytest.mark.mongo
async def test_entries_are_kept_a_year_and_read_newest_first(mongo):
    audit._indexed.discard(mongo.guild_id)
    for action in ("module.save", "module.toggle", "embeds.publish"):
        await audit.record(mongo.guild_id, ADMIN, action, target="moduleXp", changes=["x"])
    await audit.record(None, ADMIN, "extensions.reload", target="extensions.xp")

    repo = audit.AuditRepository(mongo.guild_id)
    entries = await repo.list()
    assert [e["action"] for e in entries] == ["embeds.publish", "module.toggle", "module.save"]
    assert entries[0]["username"] == "alice" and entries[0]["changes"] == ["x"]
    older = await repo.list(before=entries[0]["_id"])
    assert [e["action"] for e in older] == ["module.toggle", "module.save"]
    assert [e["action"] for e in await audit.AuditRepository(None).list()] == ["extensions.reload"]

    indexes = await mongo.guild_collection(audit.COLLECTION).index_information()
    ttl = indexes["at_ttl"]["expireAfterSeconds"]
    assert timedelta(seconds=ttl) == audit.RETENTION


@pytest.mark.mongo
async def test_the_route_returns_json_entries(mongo):
    await audit.record(mongo.guild_id, ADMIN, "module.save", target="moduleXp")
    get = endpoint(audit_routes, fake_ctx(), "GET", "/api/servers/{server_id}/audit")
    body = json.loads((await get(None, mongo.guild_id, limit=10, before=None)).body)
    (entry,) = body["entries"]
    assert entry["action"] == "module.save" and entry["at"] <= clock.now().isoformat()


@pytest.mark.mongo
async def test_entries_of_the_same_millisecond_are_all_paged_through(mongo):
    """Paging by ``at`` skipped entries sharing a timestamp (seen on CI)."""
    repo = audit.AuditRepository(mongo.guild_id)
    at = clock.now()
    for n in range(3):
        await repo.add({"at": at, "action": f"a{n}", "user_id": "7"})
    seen, before = [], None
    while page := await repo.list(limit=1, before=before):
        seen.append(page[0]["action"])
        before = page[0]["_id"]
    assert seen == ["a2", "a1", "a0"]
