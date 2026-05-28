"""Frontend HTML + static asset serving, plus the unauthenticated health probe."""

import mimetypes
import os

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from src.webui.context import WebUIContext

_STATIC_DIR = os.path.realpath(os.path.join("src", "webui", "static"))


def _serve_frontend() -> HTMLResponse:
    """Load and return the frontend HTML."""
    frontend_path = os.path.join(_STATIC_DIR, "index.html")
    try:
        with open(frontend_path, encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(
            content="<h1>Dashboard en construction</h1><p>Le fichier frontend n'a pas été trouvé.</p>",
            status_code=500,
        )


def _resolve_static(path: str) -> str | None:
    """Resolve *path* under the static dir, or return None if it escapes it."""
    candidate = os.path.realpath(os.path.join(_STATIC_DIR, path))
    if candidate != _STATIC_DIR and not candidate.startswith(_STATIC_DIR + os.sep):
        return None
    return candidate if os.path.isfile(candidate) else None


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
        resolved = _resolve_static(path)
        if resolved is not None:
            content_type, _ = mimetypes.guess_type(resolved)
            with open(resolved, "rb") as f:
                content = f.read()
            return Response(content=content, media_type=content_type or "application/octet-stream")
        return _serve_frontend()

    return router
