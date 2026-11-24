"""The dashboard SPA's sources, for the tests that read them.

``index.html`` holds the markup, ``app.css`` the styles, and ``js/`` the ES
modules (``js/main.js`` imports the others).
"""

from source_scan import ROOT

STATIC = ROOT / "src" / "webui" / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
CSS = (STATIC / "app.css").read_text(encoding="utf-8")
# Each module's path from js/ ("pages/server.js"), and its text.
MODULES = {
    path.relative_to(STATIC / "js").as_posix(): path.read_text(encoding="utf-8")
    for path in sorted((STATIC / "js").rglob("*.js"))
}
SCRIPT = "\n".join(MODULES.values())
