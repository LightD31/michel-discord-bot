"""Frontend serving + the unauthenticated health probe.

The SPA is ``index.html`` (markup), ``app.css`` (styles) and native ES modules
under ``js/`` (``js/main.js`` imports the others; the Content-Security-Policy
allows no inline script). The catch-all returns ``index.html`` for every other
path — the client-side router handles them.

Cache-busting: the version is a hash of every file under ``static/``. The page
names its stylesheet and entry module with ``?v=<version>``, and each module's
imports get the same suffix when it is served, so a versioned file never
changes and is cached for good, while the page itself is revalidated on every
load (``no-cache`` + ETag): a deploy shows up at once and an unchanged load
costs one 304.
"""

import hashlib
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from src.webui.context import WebUIContext

_STATIC_DIR = Path("src/webui/static").resolve()

SCRIPT_MEDIA_TYPE = "text/javascript; charset=utf-8"
STATIC_MEDIA_TYPES = {".js": SCRIPT_MEDIA_TYPE, ".css": "text/css; charset=utf-8"}
IMMUTABLE = "public, max-age=31536000, immutable"
# A relative import, the one form the modules use (tests/test_spa_csp.py keeps it so).
IMPORT = re.compile(r"""^(import (?:[^'"\n]* from )?)'(\.{1,2}/[\w./-]+\.js)';$""", re.MULTILINE)
PAGE_ASSETS = ("/static/app.css", "/static/js/main.js")

# Each file is read once and kept in memory, keyed on its mtime, so an edited
# file is still picked up without a restart.
_cache: dict[str, tuple[float, str]] = {}
_version: dict[tuple, str] = {}


def _read(path: Path) -> str:
    mtime = path.stat().st_mtime
    cached = _cache.get(str(path))
    if cached is None or cached[0] != mtime:
        cached = (mtime, path.read_text(encoding="utf-8"))
        _cache[str(path)] = cached
    return cached[1]


def _etag(content: str) -> str:
    return '"' + hashlib.sha256(content.encode("utf-8")).hexdigest()[:32] + '"'


def static_version() -> str:
    """A hash of every static file's name and content (recomputed when one changes)."""
    files = sorted(p for p in _STATIC_DIR.rglob("*") if p.is_file())
    key = tuple((str(p), p.stat().st_mtime) for p in files)
    if key not in _version:
        digest = hashlib.sha256()
        for p in files:
            digest.update(str(p.relative_to(_STATIC_DIR)).encode())
            digest.update(_read(p).encode("utf-8") if p.suffix in STATIC_MEDIA_TYPES else b"")
        _version.clear()
        _version[key] = digest.hexdigest()[:16]
    return _version[key]


def versioned_imports(script: str, version: str) -> str:
    """*script* with ``?v=<version>`` on each relative import."""
    return IMPORT.sub(lambda m: f"{m.group(1)}'{m.group(2)}?v={version}';", script)


def _respond(content: str, media_type: str, request: Request | None, cache: str) -> Response:
    """*content*, or a 304 when the browser already has this version."""
    etag = _etag(content)
    headers = {"ETag": etag, "Cache-Control": cache}
    if request is not None and etag in request.headers.get("if-none-match", ""):
        return Response(status_code=304, headers=headers)
    return Response(content=content, media_type=media_type, headers=headers)


def _serve_frontend(request: Request | None = None) -> Response:
    """The page, naming its stylesheet and entry module by version."""
    try:
        page = _read(_STATIC_DIR / "index.html")
    except FileNotFoundError:
        return HTMLResponse(
            content="<h1>Dashboard en construction</h1><p>Le fichier frontend n'a pas été trouvé.</p>",
            status_code=500,
        )
    version = static_version()
    for asset in PAGE_ASSETS:
        page = page.replace(f'"{asset}"', f'"{asset}?v={version}"')
    # Revalidated on every load (no-cache), so a deploy shows up right away.
    return _respond(page, "text/html; charset=utf-8", request, "no-cache")


def _serve_static(path: str, request: Request) -> Response:
    """A script or stylesheet from the static folder, and nothing else."""
    root = _STATIC_DIR.resolve()
    target = (root / path).resolve()
    media_type = STATIC_MEDIA_TYPES.get(target.suffix)
    if media_type is None or not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(status_code=404, detail="Fichier introuvable")
    version = static_version()
    content = _read(target)
    if target.suffix == ".js":
        content = versioned_imports(content, version)
    # A file asked for under the current version never changes: cached for
    # good. Any other request (no version, or a page from before a deploy) is
    # revalidated, so it never pins a stale copy.
    cache = IMMUTABLE if request.query_params.get("v") == version else "no-cache"
    return _respond(content, media_type, request, cache)


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    async def health():
        """Unauthenticated liveness probe."""
        return JSONResponse({"status": "ok"})

    @router.get("/static/{path:path}")
    async def static_file(request: Request, path: str):
        """The SPA's modules and stylesheet (separate files: the CSP allows no inline script)."""
        return _serve_static(path, request)

    @router.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        """Serve the main dashboard page."""
        return _serve_frontend(request)

    @router.get("/{path:path}", response_class=HTMLResponse)
    async def catch_all(request: Request, path: str):
        """Fall back to the SPA for any unknown path; client-side router takes over."""
        return _serve_frontend(request)

    return router
