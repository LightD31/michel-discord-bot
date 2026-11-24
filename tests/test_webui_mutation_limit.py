"""Mutating dashboard requests are rate limited per session."""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from src.webui.mutation_limit import MutationRateLimitMiddleware
from src.webui.ratelimit import RateLimiter


def make_client(limit=2, preview_limit=3) -> TestClient:
    app = FastAPI()

    @app.post("/api/servers/{server_id}/template-preview")
    async def preview(server_id: str):  # noqa: ARG001
        return JSONResponse({"ok": True})

    @app.post("/api/thing")
    async def change():
        return JSONResponse({"ok": True})

    @app.get("/api/thing")
    async def read():
        return JSONResponse({"ok": True})

    @app.post("/auth/callback")
    async def auth():
        return JSONResponse({"ok": True})

    app.add_middleware(
        MutationRateLimitMiddleware,
        limiter=RateLimiter(limit, 60.0),
        preview_limiter=RateLimiter(preview_limit, 60.0),
    )
    return TestClient(app)


def test_a_session_is_throttled_after_its_budget():
    client = make_client()
    cookies = {"michel_session": "alice"}
    assert [client.post("/api/thing", cookies=cookies).status_code for _ in range(2)] == [200, 200]
    refused = client.post("/api/thing", cookies=cookies)
    assert refused.status_code == 429
    assert int(refused.headers["retry-after"]) >= 1
    assert "réessaie" in refused.json()["detail"]


def test_reads_and_non_api_routes_are_not_counted():
    client = make_client(limit=1)
    cookies = {"michel_session": "alice"}
    for _ in range(5):
        assert client.get("/api/thing", cookies=cookies).status_code == 200
        assert client.post("/auth/callback", cookies=cookies).status_code == 200
    assert client.post("/api/thing", cookies=cookies).status_code == 200


def test_each_session_has_its_own_budget():
    client = make_client(limit=1)
    assert client.post("/api/thing", cookies={"michel_session": "alice"}).status_code == 200
    assert client.post("/api/thing", cookies={"michel_session": "bob"}).status_code == 200
    assert client.post("/api/thing", cookies={"michel_session": "alice"}).status_code == 429


def test_the_template_preview_has_a_budget_of_its_own():
    """Typing in a template never uses up the budget a save needs, and is limited too."""
    client = make_client(limit=1, preview_limit=3)
    cookies = {"michel_session": "alice"}
    preview = "/api/servers/123/template-preview"
    assert [client.post(preview, cookies=cookies).status_code for _ in range(4)] == [
        200,
        200,
        200,
        429,
    ]
    assert client.post("/api/thing", cookies=cookies).status_code == 200
    # Only that route: another path ending the same way counts as a change.
    assert client.post("/api/thing/template-preview", cookies=cookies).status_code == 429
