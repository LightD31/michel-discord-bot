"""Log retrieval + real-time SSE log streaming."""

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from src.webui.context import WebUIContext
from src.webui.log_handler import WebUILogHandler


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

        async def event_generator():
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        entry = await asyncio.wait_for(queue.get(), timeout=30.0)
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
