"""Log retrieval + real-time SSE log streaming."""

import asyncio
import json
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from src.webui.context import WebUIContext
from src.webui.log_handler import WebUILogHandler

# How often an open stream re-checks that its viewer is still a developer with
# a live session: the check at opening alone let a revoked id (or a logged-out
# session) keep reading the logs for as long as the tab stayed open.
RECHECK_SECONDS = 30.0
PING_SECONDS = 30.0


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/logs")
    async def api_get_logs(
        request: Request,
        count: int = 200,
        level: str = "",
        search: str = "",
        logger_name: str = "",
    ):
        """Get recent log entries with optional filtering."""
        ctx.require_developer(request)
        handler = WebUILogHandler.get_instance()
        if not handler:
            return JSONResponse({"logs": []})
        logs = handler.get_recent(
            count=min(count, 2000),
            level=level or None,
            search=search or None,
            logger_name=logger_name or None,
        )
        return JSONResponse({"logs": logs})

    @router.get("/api/logs/stream")
    async def api_stream_logs(request: Request):
        """SSE endpoint for real-time log streaming."""
        ctx.require_developer(request)
        handler = WebUILogHandler.get_instance()
        if not handler:
            raise HTTPException(status_code=503, detail="Log handler non disponible")

        queue = handler.subscribe()

        def still_allowed() -> bool:
            try:
                ctx.require_developer(request)
            except HTTPException:
                return False
            return True

        async def event_generator():
            checked_at = time.monotonic()
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    if time.monotonic() - checked_at >= RECHECK_SECONDS:
                        if not still_allowed():
                            yield {"event": "revoked", "data": ""}
                            break
                        checked_at = time.monotonic()
                    try:
                        entry = await asyncio.wait_for(queue.get(), timeout=PING_SECONDS)
                        yield {
                            "event": "log",
                            "data": json.dumps(entry.to_dict()),
                        }
                    except TimeoutError:
                        yield {"event": "ping", "data": ""}
            except (asyncio.CancelledError, GeneratorExit):
                pass
            finally:
                handler.unsubscribe(queue)

        return EventSourceResponse(event_generator())

    return router
