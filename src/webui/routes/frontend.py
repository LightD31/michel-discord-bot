"""Frontend HTML serving + the unauthenticated health probe.

The catch-all just returns the SPA — the client-side router handles all
unknown paths. To serve real static assets later, mount
``fastapi.staticfiles.StaticFiles`` at a prefix in ``src/webui/app.py``.
"""

from pathlib import Path

from fastapi import APIRouter, Request
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
        """Fall back to the SPA for any unknown path; client-side router takes over."""
        return _serve_frontend()

    return router
