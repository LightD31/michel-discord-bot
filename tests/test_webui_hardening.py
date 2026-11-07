"""Web UI hardening: security headers, module-name/size limits, backup contents."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import src.core.config as core_config
import src.webui.routes.servers as servers_routes
from src.core import backup_format
from src.core.db import BACKUP_CONFIG_FILENAME, BACKUP_EXCLUDED_COLLECTIONS, MongoManager
from src.webui import sessions
from src.webui.ratelimit import TrustedProxies
from src.webui.security_headers import SecurityHeadersMiddleware

# ── Security headers ────────────────────────────────────────────────────


def make_app(trusted: TrustedProxies | None = None) -> FastAPI:
    app = FastAPI()

    @app.get("/plain")
    async def plain():
        return JSONResponse({"ok": True})

    @app.get("/custom")
    async def custom():
        return JSONResponse({"ok": True}, headers={"X-Frame-Options": "SAMEORIGIN"})

    app.add_middleware(SecurityHeadersMiddleware, trusted_proxies=trusted)
    return app


def test_every_response_carries_the_hardening_headers():
    resp = TestClient(make_app()).get("/plain")
    assert resp.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in resp.headers["content-security-policy"]
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["referrer-policy"] == "same-origin"
    assert "strict-transport-security" not in resp.headers  # plain HTTP: never pinned


def test_hsts_only_over_https():
    resp = TestClient(make_app(), base_url="https://michel.example").get("/plain")
    assert resp.headers["strict-transport-security"].startswith("max-age=")


def test_forwarded_proto_believed_from_trusted_proxies_only():
    # TestClient's peer is "testclient", which is no proxy of ours.
    resp = TestClient(make_app(TrustedProxies())).get(
        "/plain", headers={"X-Forwarded-Proto": "https"}
    )
    assert "strict-transport-security" not in resp.headers


def test_route_headers_win():
    resp = TestClient(make_app()).get("/custom")
    assert resp.headers.get_list("x-frame-options") == ["SAMEORIGIN"]


# ── Module saves: known module names, bounded size ──────────────────────


class FakeCtx:
    bot = None

    def __init__(self):
        self.mutations = 0

    async def require_guild_admin(self, request, guild_id):  # noqa: ARG002 — signature parity
        return SimpleNamespace(username="admin", user_id="1")

    def mutate_config(self, mutator):
        self.mutations += 1
        data = {"servers": {}}
        mutator(data)
        return data

    def bot_loop_alive(self):
        return False

    def get_full_config(self):
        return {"config": {}, "servers": {}}


def module_endpoint(ctx, method: str, suffix: str = ""):
    router = servers_routes.create_router(ctx)
    path = "/api/servers/{server_id}/modules/{module_name}" + suffix
    return next(r for r in router.routes if r.path == path and method in r.methods).endpoint


def test_unknown_module_names_are_rejected():
    """servers.<id>.<anything> must not be writable (e.g. the public dashboard token)."""
    ctx = FakeCtx()
    put = module_endpoint(ctx, "PUT")
    toggle = module_endpoint(ctx, "POST", "/toggle")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            put(None, "1", "publicDashboardToken", servers_routes.ConfigUpdate(config={"x": 1}))
        )
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            toggle(
                None,
                "1",
                "publicDashboardToken",
                servers_routes.ModuleToggle(module="publicDashboardToken", enabled=True),
            )
        )
    assert exc.value.status_code == 404
    assert ctx.mutations == 0


def test_known_module_saves_and_oversized_config_is_refused():
    ctx = FakeCtx()
    put = module_endpoint(ctx, "PUT")
    resp = asyncio.run(
        put(None, "1", "moduleXp", servers_routes.ConfigUpdate(config={"enabled": False}))
    )
    assert json.loads(resp.body)["status"] == "ok"
    assert ctx.mutations == 1

    huge = {"blob": "x" * (servers_routes.MAX_MODULE_CONFIG_BYTES + 1)}
    with pytest.raises(HTTPException) as exc:
        asyncio.run(put(None, "1", "moduleXp", servers_routes.ConfigUpdate(config=huge)))
    assert exc.value.status_code == 413
    assert ctx.mutations == 1


# ── Backups ─────────────────────────────────────────────────────────────


class FakeCursor:
    def __init__(self, docs):
        self.docs = docs

    def __aiter__(self):
        async def gen():
            for doc in self.docs:
                yield dict(doc)

        return gen()


class FakeDb:
    def __init__(self, collections):
        self.collections = collections

    async def list_collection_names(self):
        return list(self.collections)

    def __getitem__(self, name):
        return SimpleNamespace(find=lambda: FakeCursor(self.collections[name]))


class FakeClient:
    def __init__(self, dbs):
        self.dbs = dbs

    async def list_database_names(self):
        return list(self.dbs)

    def __getitem__(self, name):
        return FakeDb(self.dbs[name])


def test_sessions_collection_is_the_excluded_one():
    assert sessions.BACKUP_KEY in BACKUP_EXCLUDED_COLLECTIONS


def test_backup_skips_sessions_and_copies_config(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    config_file.write_text('{"config": {}, "servers": {}}', encoding="utf-8")
    monkeypatch.setattr(core_config, "CONFIG_PATH", str(config_file))
    fake = FakeClient(
        {
            "global": {"webui_sessions": [{"_id": "hash"}], "olympics": [{"_id": 1}]},
            "guild_1": {"xp": [{"_id": 2, "xp": 10}]},
            "admin": {"system.users": [{"_id": 3}]},  # not ours: never backed up
        }
    )
    monkeypatch.setattr(MongoManager, "client", property(lambda self: fake))

    dest = asyncio.run(MongoManager().backup_all(backup_dir=str(tmp_path / "backups")))

    root = tmp_path / "backups"
    folder = next(root.iterdir())
    assert str(folder) == dest
    assert not (folder / "global" / "webui_sessions.jsonl.gz").exists()
    olympics = backup_format.collection_path(folder, "global", "olympics")
    assert list(backup_format.read_collection(olympics)) == [{"_id": 1}]
    assert backup_format.collection_path(folder, "guild_1", "xp").exists()
    assert not (folder / "admin").exists()
    assert backup_format.read_manifest(folder)["databases"] == {
        "global": {"olympics": 1},
        "guild_1": {"xp": 1},
    }
    copied = folder / BACKUP_CONFIG_FILENAME
    assert copied.read_text(encoding="utf-8") == config_file.read_text(encoding="utf-8")
    assert copied.stat().st_mode & 0o077 == 0  # owner-only


def test_manual_backup_never_lowers_retention():
    from extensions.backup import effective_retention

    assert effective_retention(None, 7) == 7
    assert effective_retention(1, 7) == 7  # would otherwise prune the whole history
    assert effective_retention(20, 7) == 20


# ── Route → bot-loop calls ──────────────────────────────────────────────


def bot_ctx():
    from src.webui.auth import DiscordOAuth
    from src.webui.context import WebUIContext

    oauth = DiscordOAuth(client_id="", client_secret="", redirect_uri="")
    return WebUIContext(bot=SimpleNamespace(), bot_loop=None, oauth=oauth)


def run_on_own_loop(ctx, coro_factory):
    async def main():
        ctx.bot_loop = asyncio.get_running_loop()
        return await coro_factory()

    return asyncio.run(main())


def test_call_bot_returns_the_result():
    ctx = bot_ctx()

    async def work():
        return 42

    assert run_on_own_loop(ctx, lambda: ctx.call_bot(work, what="answer")) == 42


def test_call_bot_passes_http_errors_through():
    ctx = bot_ctx()

    async def missing():
        raise HTTPException(status_code=404, detail="Serveur introuvable")

    with pytest.raises(HTTPException) as exc:
        run_on_own_loop(ctx, lambda: ctx.call_bot(missing, what="lookup"))
    assert exc.value.status_code == 404


def test_call_bot_hides_internal_errors():
    ctx = bot_ctx()

    async def boom():
        raise RuntimeError("mongodb://admin:hunter2@db:27017 refused")  # pragma: allowlist secret

    with pytest.raises(HTTPException) as exc:
        run_on_own_loop(ctx, lambda: ctx.call_bot(boom, what="list members"))
    assert exc.value.status_code == 500
    assert "hunter2" not in exc.value.detail and "mongodb" not in exc.value.detail


def test_call_bot_times_out():
    ctx = bot_ctx()

    async def stuck():
        await asyncio.sleep(5)

    with pytest.raises(HTTPException) as exc:
        run_on_own_loop(ctx, lambda: ctx.call_bot(stuck, what="stuck", timeout=0.05))
    assert exc.value.status_code == 504


# ── SPA caching ─────────────────────────────────────────────────────────


def test_spa_is_cached_and_revalidated(tmp_path, monkeypatch):
    import os

    import src.webui.routes.frontend as frontend

    page = tmp_path / "index.html"
    page.write_text("<h1>v1</h1>", encoding="utf-8")
    monkeypatch.setattr(frontend, "_STATIC_DIR", tmp_path)
    frontend._cache.clear()
    app = FastAPI()
    app.include_router(frontend.create_router(None))
    client = TestClient(app)

    first = client.get("/")
    assert first.status_code == 200 and first.text == "<h1>v1</h1>"
    etag = first.headers["etag"]
    assert first.headers["cache-control"] == "no-cache"

    again = client.get("/servers/1", headers={"If-None-Match": etag})
    assert again.status_code == 304 and again.content == b""

    page.write_text("<h1>v2</h1>", encoding="utf-8")
    stat = page.stat()
    os.utime(page, (stat.st_atime, stat.st_mtime + 5))  # an edit is picked up without restart
    edited = client.get("/", headers={"If-None-Match": etag})
    assert edited.status_code == 200 and edited.text == "<h1>v2</h1>"
    assert edited.headers["etag"] != etag
