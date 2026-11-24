"""The live log stream ends once its viewer is no longer a developer."""

import asyncio

from fastapi import HTTPException

from src.webui.sse import logs as logs_sse


class Handler:
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.unsubscribed = False

    def subscribe(self):
        return self.queue

    def unsubscribe(self, queue):
        self.unsubscribed = True


class Entry:
    def to_dict(self):
        return {"message": "hello"}


class Ctx:
    def __init__(self):
        self.developer = True

    def require_developer(self, request):
        if not self.developer:
            raise HTTPException(status_code=403)


class Req:
    async def is_disconnected(self):
        return False


async def test_a_revoked_developer_stops_receiving_logs(monkeypatch):
    handler = Handler()
    monkeypatch.setattr(logs_sse.WebUILogHandler, "get_instance", staticmethod(lambda: handler))
    monkeypatch.setattr(logs_sse, "RECHECK_SECONDS", 0.0)
    monkeypatch.setattr(logs_sse, "PING_SECONDS", 0.01)
    ctx = Ctx()
    router = logs_sse.create_router(ctx)
    stream = next(r.endpoint for r in router.routes if r.path == "/api/logs/stream")

    response = await stream(Req())
    events = response.body_iterator
    handler.queue.put_nowait(Entry())
    first = await anext(events)
    assert first["event"] == "log"

    ctx.developer = False  # removed from developerUserIds, or logged out
    handler.queue.put_nowait(Entry())
    second = await anext(events)
    assert second["event"] == "revoked"
    try:
        await anext(events)
        ended = False
    except StopAsyncIteration:
        ended = True
    assert ended and handler.unsubscribed
