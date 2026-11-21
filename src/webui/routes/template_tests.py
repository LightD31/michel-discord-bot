"""Try a message template from the dashboard: the bot DMs the result (F11).

See :mod:`src.webui.template_tests` for the rendering. The admin's DM is sent
from the bot loop; a member whose DMs are closed gets a 409 saying so.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from interactions.client.errors import Forbidden
from pydantic import BaseModel, Field

from src.core import logging as logutil
from src.discord_ext.mentions import NO_PINGS
from src.webui import audit
from src.webui.context import WebUIContext
from src.webui.template_tests import (
    MAX_TEXTS,
    TESTS,
    TemplateRefused,
    catalogue,
    dm_text,
    preview_texts,
    render_test,
    sample_values,
)

logger = logutil.init_logger("webui.routes.template_tests")

DMS_CLOSED = (
    "Impossible de t'envoyer un MP : autorise les messages privés des membres de ce serveur, "
    "puis réessaie."
)


class TemplateTestBody(BaseModel):
    module: str
    field: str
    texts: list[str] = Field(min_length=1, max_length=MAX_TEXTS * 5)


async def send_test_dms(bot: Any, user_id: str, messages: list[str]) -> None:
    """DM *messages* to the dashboard user (on the bot loop)."""
    user = await bot.fetch_user(int(user_id))
    if user is None:
        raise HTTPException(status_code=404, detail="Compte Discord introuvable")
    try:
        for content in messages:
            await user.send(content, allowed_mentions=NO_PINGS)
    except Forbidden as e:
        raise HTTPException(status_code=409, detail=DMS_CLOSED) from e


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/template-tests")
    async def api_template_tests(request: Request):
        ctx.require_admin(request)
        return JSONResponse(catalogue())

    @router.post("/api/servers/{server_id}/template-preview")
    async def api_template_preview(request: Request, server_id: str, body: TemplateTestBody):
        """The form's texts as the bot would send them, for the live preview (C4).

        Rendered by the same function as the DM test and production, with the
        admin standing in for the member; nothing is sent or recorded. The SPA
        draws the result (Markdown, mentions by name) without adding to it.
        """
        session = await ctx.require_guild_admin(request, server_id)
        test = TESTS.get(body.module, {}).get(body.field)
        if test is None:
            raise HTTPException(status_code=404, detail="Ce champ n'a pas d'aperçu")
        values = sample_values(session.user_id, session.username)
        return JSONResponse({"previews": preview_texts(test, body.texts, values)})

    @router.post("/api/servers/{server_id}/template-test")
    async def api_template_test(request: Request, server_id: str, body: TemplateTestBody):
        session = await ctx.require_guild_admin(request, server_id)
        test = TESTS.get(body.module, {}).get(body.field)
        if test is None:
            raise HTTPException(status_code=404, detail="Ce champ ne se teste pas")
        texts = [t for t in body.texts if t.strip()]
        if not texts:
            raise HTTPException(status_code=422, detail="Aucun message à tester")
        try:
            rendered = render_test(test, texts, sample_values(session.user_id, session.username))
        except TemplateRefused as e:
            raise HTTPException(status_code=422, detail=str(e)) from e

        guild = ctx.bot.get_guild(int(server_id)) if ctx.bot else None
        guild_name = str(getattr(guild, "name", None) or server_id)
        total = len(rendered.texts)
        messages = [
            dm_text(test, guild_name, text, index, total)
            for index, text in enumerate(rendered.texts, start=1)
        ]
        ctx.require_bot_loop()
        await ctx.call_bot(
            send_test_dms, ctx.bot, session.user_id, messages, what="send a template test DM"
        )
        await audit.record(
            server_id,
            session,
            "template.test",
            target=body.module,
            changes=[body.field],
            details={"messages": total},
        )
        return JSONResponse(
            {"sent": total, "previews": rendered.texts, "warnings": rendered.warnings}
        )

    return router
