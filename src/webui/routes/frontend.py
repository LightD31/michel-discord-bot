"""Frontend HTML + static asset serving, plus the unauthenticated health probe."""

import mimetypes
from pathlib import Path

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from src.webui.context import WebUIContext

_STATIC_DIR = Path("src/webui/static").resolve()


def _serve_frontend() -> HTMLResponse:
    """Load and return the frontend HTML."""
    frontend_path = _STATIC_DIR / "index.html"
    try:
        return HTMLResponse(content=frontend_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return HTMLResponse(
            content="<h1>Dashboard en construction</h1><p>Le fichier frontend n'a pas été trouvé.</p>",
            status_code=500,
        )


def _resolve_static(path: str) -> Path | None:
    """Resolve *path* under the static dir, or return None if it escapes it."""
    candidate = (_STATIC_DIR / path).resolve()
    try:
        candidate.relative_to(_STATIC_DIR)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


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
            content_type, _ = mimetypes.guess_type(str(resolved))
            return Response(
                content=resolved.read_bytes(),
                media_type=content_type or "application/octet-stream",
            )
        return _serve_frontend()

    return router
