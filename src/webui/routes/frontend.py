"""Frontend serving + the unauthenticated health probe.

The SPA is two files: ``index.html`` (markup and styles) and ``app.js`` (its
script, served at ``/static/app.js``: the Content-Security-Policy allows no
inline script). The catch-all returns ``index.html`` for every other path —
the client-side router handles them.
"""

import hashlib
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from src.webui.context import WebUIContext

_STATIC_DIR = Path("src/webui/static").resolve()

# The SPA's files are read once and kept in memory with their ETag, instead of
# being read from disk on every page load. Keyed on the file's mtime, so an
# edited file is still picked up without a restart.
_cache: dict[str, tuple[float, str, str]] = {}
SCRIPT_MEDIA_TYPE = "text/javascript; charset=utf-8"


def _load_frontend(path: Path) -> tuple[str, str]:
    """``(html, etag)`` for *path*, re-read only when the file changed."""
    mtime = path.stat().st_mtime
    cached = _cache.get(str(path))
    if cached is None or cached[0] != mtime:
        html = path.read_text(encoding="utf-8")
        etag = '"' + hashlib.sha256(html.encode("utf-8")).hexdigest()[:32] + '"'
        _cache[str(path)] = (mtime, html, etag)
        cached = _cache[str(path)]
    return cached[1], cached[2]


def _serve_file(name: str, media_type: str, request: Request | None) -> Response:
    """Static file *name*, or a 304 when the browser already has this version."""
    try:
        content, etag = _load_frontend(_STATIC_DIR / name)
    except FileNotFoundError:
        return HTMLResponse(
            content="<h1>Dashboard en construction</h1><p>Le fichier frontend n'a pas été trouvé.</p>",
            status_code=500,
        )
    # Revalidate on every load (no-cache), so a deploy shows up right away.
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    if request is not None and etag in request.headers.get("if-none-match", ""):
        return Response(status_code=304, headers=headers)
    return Response(content=content, media_type=media_type, headers=headers)


def _serve_frontend(request: Request | None = None) -> Response:
    """The frontend HTML, or a 304 when the browser already has this version."""
    return _serve_file("index.html", "text/html; charset=utf-8", request)


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    async def health():
        """Unauthenticated liveness probe."""
        return JSONResponse({"status": "ok"})

    @router.get("/static/app.js")
    async def script(request: Request):
        """The SPA's script (a separate file: the CSP allows no inline script)."""
        return _serve_file("app.js", SCRIPT_MEDIA_TYPE, request)

    @router.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        """Serve the main dashboard page."""
        return _serve_frontend(request)

    @router.get("/{path:path}", response_class=HTMLResponse)
    async def catch_all(request: Request, path: str):
        """Fall back to the SPA for any unknown path; client-side router takes over."""
        return _serve_frontend(request)

    return router
