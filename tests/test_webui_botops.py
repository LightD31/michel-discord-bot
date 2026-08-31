"""The dashboard runs in its own thread — bot mutations must reach the bot loop.

Calling ``bot.reload_extension(...)`` inline on the Web UI's uvicorn loop makes
interactions.py schedule its ``synchronise_interactions()`` task on *that* loop,
where the client's aiohttp session doesn't live: it dies with "Timeout context
manager should be used inside a task" and, unawaited, only shows up as a stray
"Task exception was never retrieved". These tests pin the dispatch instead.
"""

import asyncio
import threading

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.webui.botops import run_extension_op, sync_commands
from src.webui.context import WebUIContext
from src.webui.routes.extensions import create_router
from src.webui.routes.servers import _try_reload_extension_for_module


class _FakeBot:
    """Records the loop each call ran on, and ``sync_ext`` as seen at call time."""

    def __init__(self) -> None:
        self.sync_ext = True
        self.calls: list[tuple] = []
        self.sync_calls: list[tuple] = []
        self.fail: Exception | None = None

    def _record(self, action: str, name: str) -> None:
        self.calls.append((action, name, asyncio.get_running_loop(), self.sync_ext))
        if self.fail:
            raise self.fail

    def load_extension(self, name):
        self._record("load", name)

    def unload_extension(self, name):
        self._record("unload", name)

    def reload_extension(self, name):
        self._record("reload", name)

    async def synchronise_interactions(self, *, scopes=None):
        self.sync_calls.append((scopes, asyncio.get_running_loop()))


@pytest.fixture
def bot_loop():
    """A second event loop in its own thread, standing in for the bot's."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    yield loop
    if not loop.is_closed():  # a test may have closed it on purpose
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()


def _ctx(bot, loop):
    return WebUIContext(bot=bot, bot_loop=loop, oauth=None)  # type: ignore[arg-type]


async def test_extension_op_runs_on_the_bot_loop(bot_loop):
    bot = _FakeBot()
    await run_extension_op(_ctx(bot, bot_loop), "reload", "extensions.xp")

    action, name, loop, sync_ext_during = bot.calls[0]
    assert (action, name) == ("reload", "extensions.xp")
    assert loop is bot_loop
    assert loop is not asyncio.get_running_loop()
    # The library's fire-and-forget sync task is suppressed during the call...
    assert sync_ext_during is False
    # ...and the client is left as we found it.
    assert bot.sync_ext is True


async def test_extension_op_restores_sync_ext_when_the_load_fails(bot_loop):
    bot = _FakeBot()
    bot.fail = RuntimeError("bad extension")

    with pytest.raises(RuntimeError, match="bad extension"):
        await run_extension_op(_ctx(bot, bot_loop), "load", "extensions.broken")

    assert bot.sync_ext is True


async def test_sync_commands_runs_on_the_bot_loop(bot_loop):
    bot = _FakeBot()
    await sync_commands(_ctx(bot, bot_loop), scopes=[123])
    await sync_commands(_ctx(bot, bot_loop))

    assert [scopes for scopes, _ in bot.sync_calls] == [[123], None]
    assert all(loop is bot_loop for _, loop in bot.sync_calls)


async def test_run_on_bot_loop_times_out_without_wedging_the_request(bot_loop):
    async def _slow():
        await asyncio.sleep(30)

    with pytest.raises(TimeoutError):
        await _ctx(_FakeBot(), bot_loop).run_on_bot_loop(_slow, timeout=0.05)


async def test_run_on_bot_loop_reports_a_dead_bot(bot_loop):
    bot_loop.call_soon_threadsafe(bot_loop.stop)
    await asyncio.sleep(0.05)
    bot_loop.close()

    with pytest.raises(HTTPException) as exc:
        await _ctx(_FakeBot(), bot_loop).run_on_bot_loop(lambda: None, timeout=1)
    assert exc.value.status_code == 503


async def test_module_reload_reports_a_missing_bot_instead_of_raising():
    result = await _try_reload_extension_for_module(_ctx(None, None), "moduleXP")
    assert result == {"reloaded": None, "error": "Bot non disponible", "skipped": False}


class _DevCtx(WebUIContext):
    """Context that skips the OAuth handshake — the routes only need the hook."""

    def require_developer(self, request):  # noqa: ARG002 — signature parity
        return object()


async def test_reload_route_dispatches_and_syncs_once(bot_loop):
    bot = _FakeBot()
    app = FastAPI()
    app.include_router(create_router(_DevCtx(bot=bot, bot_loop=bot_loop, oauth=None)))  # type: ignore[arg-type]

    with TestClient(app) as client:
        body = client.post("/api/reload/extensions.xp").json()

    assert body["commandSync"] == {"synced": True, "error": None}
    assert [(action, name) for action, name, _, _ in bot.calls] == [("reload", "extensions.xp")]
    assert all(call[2] is bot_loop for call in bot.calls)
    # One sync for the reload, not the two unawaited ones the client would fire.
    assert bot.sync_calls == [(None, bot_loop)]
