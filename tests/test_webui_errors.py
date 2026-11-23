"""One answer for an outage, whatever the route (P31, src/webui/errors.py)."""

import asyncio
import threading

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.core.errors import DatabaseError
from src.webui.context import WebUIContext
from src.webui.errors import DB_UNAVAILABLE, INTERNAL_ERROR, install_error_handlers


def app_raising(error: Exception) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    async def boom():
        raise error

    return TestClient(app, raise_server_exceptions=False)


def test_a_database_outage_is_a_503_without_the_drivers_text():
    response = app_raising(DatabaseError("mongodb://admin:hunter2@db unreachable")).get("/boom")
    assert response.status_code == 503
    assert response.json() == {"detail": DB_UNAVAILABLE}


def test_anything_else_is_the_generic_500_without_its_text():
    response = app_raising(ValueError("internal detail")).get("/boom")
    assert response.status_code == 500
    assert response.json() == {"detail": INTERNAL_ERROR}


def test_a_refusal_keeps_its_own_answer():
    response = app_raising(HTTPException(status_code=409, detail="Déjà fait.")).get("/boom")
    assert (response.status_code, response.json()) == (409, {"detail": "Déjà fait."})


async def test_call_bot_lets_an_outage_through_to_the_503():
    """It turned a DatabaseError raised on the bot loop into a 500."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        ctx = WebUIContext(bot=object(), bot_loop=loop, oauth=None)  # type: ignore[arg-type]

        async def down():
            raise DatabaseError("down")

        with pytest.raises(DatabaseError):
            await ctx.call_bot(down, what="test")
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()
