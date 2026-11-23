"""How the dashboard answers what a route doesn't handle itself (P31).

A database outage is a 503 with one French sentence, whatever the route: a
repository raises ``DatabaseError`` (MongoDB down, or no MongoDB configured)
and the route lets it go. Anything else unexpected is the generic French 500
``ctx.call_bot`` already gives; its traceback goes to the logs, never to the
page. A route that can still answer without the database (a card that says
"unavailable" in place) catches ``DatabaseError`` itself.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.core import logging as logutil
from src.core.errors import DatabaseError

logger = logutil.init_logger("webui.errors")

DB_UNAVAILABLE = "Base de données injoignable, réessaie dans un instant."
INTERNAL_ERROR = "Erreur interne — détails dans les logs du bot."


async def _database_unavailable(request: Request, exc: Exception) -> JSONResponse:
    logger.warning("%s %s: database unavailable: %s", request.method, request.url.path, exc)
    return JSONResponse({"detail": DB_UNAVAILABLE}, status_code=503)


async def _internal_error(request: Request, exc: Exception) -> JSONResponse:
    # Starlette re-raises after answering: uvicorn logs the traceback once.
    return JSONResponse({"detail": INTERNAL_ERROR}, status_code=500)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DatabaseError, _database_unavailable)
    app.add_exception_handler(Exception, _internal_error)


__all__ = ["DB_UNAVAILABLE", "INTERNAL_ERROR", "install_error_handlers"]
