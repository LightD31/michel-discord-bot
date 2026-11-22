"""The server page's "Incidents" card (P21, C10).

``GET /api/servers/{id}/incidents`` gives the guild's admins its last failed
interactions and tasks (:mod:`src.core.incidents`): the reference a member was
told, what failed, when and a short reason, newest first. A button or a form
is named by its kind and the module prefix of its custom id, never the rest
(which may hold ids); a task by its label from the health card.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.core.incidents import Incident, incidents_of
from src.webui.context import WebUIContext
from src.webui.guild_health import TASK_LABELS

KINDS = {"component": "Bouton", "modal": "Formulaire"}


def where_label(where: str) -> str:
    if where.startswith("/"):
        return where
    kind, _, custom_id = where.partition(":")
    if kind in KINDS:
        prefix = custom_id.split(":", 1)[0].split("_", 1)[0]
        return f"{KINDS[kind]} ({prefix})" if prefix and not prefix.isdigit() else KINDS[kind]
    return TASK_LABELS.get(where, "Tâche de fond")


def incident_json(incident: Incident) -> dict[str, object]:
    return {
        "at": incident.at,
        "reference": incident.reference,
        "where": where_label(incident.where),
        "reason": incident.reason,
    }


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/servers/{server_id}/incidents")
    async def api_incidents(request: Request, server_id: str, ref: str = ""):
        await ctx.require_guild_admin(request, server_id)
        wanted = ref.strip().lower()
        found = [
            incident_json(i)
            for i in incidents_of(server_id)
            if not wanted or wanted in i.reference.lower()
        ]
        return JSONResponse({"incidents": found})

    return router


__all__ = ["create_router", "incident_json", "where_label"]
