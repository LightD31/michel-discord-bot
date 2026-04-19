"""Frontend HTML + static asset serving, plus the unauthenticated health probe."""

import mimetypes
import os

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from src.webui.context import WebUIContext


def _serve_frontend() -> HTMLResponse:
    """Load and return the frontend HTML."""
    frontend_path = os.path.join("src", "webui", "static", "index.html")
    try:
        with open(frontend_path, encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(
            content="<h1>Dashboard en construction</h1><p>Le fichier frontend n'a pas été trouvé.</p>",
            status_code=500,
        )


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    async def health():
        """Unauthenticated liveness probe."""
        return JSONResponse({"status": "ok"})

    @router.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        """Serve the main dashboard page."""
        return _serve_frontend()

    @router.get("/{path:path}", response_class=HTMLResponse)
    async def catch_all(request: Request, path: str):
        """Serve static files or fall back to the SPA frontend."""
        static_path = os.path.join("src", "webui", "static", path)
        if os.path.isfile(static_path):
            content_type, _ = mimetypes.guess_type(static_path)
            with open(static_path, encoding="utf-8") as f:
                content = f.read()
            return Response(content=content, media_type=content_type or "text/plain")
        return _serve_frontend()

    return router
