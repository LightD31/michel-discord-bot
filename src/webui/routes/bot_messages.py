"""The messages the bot keeps up to date in a guild: list them, republish one.

Where each lives is stored in the guild's ``bot_messages`` collection
(:mod:`src.core.message_refs`), not in the config. Republishing deletes the
bot's own message and its reference, so the module's next refresh posts it
again at the bottom of its channel. Only messages the bot posted itself are
ever referenced there, and a guild admin could already force a new post by
changing the module's channel, so this grants no Discord power of its own.
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.core import logging as logutil
from src.core.message_refs import MessageRef, MessageRefRepository
from src.discord_ext.messages import republish_persistent_message
from src.webui.context import WebUIContext

logger = logutil.init_logger("webui.routes.bot_messages")


class RepublishBody(BaseModel):
    slot: str = Field(min_length=1, max_length=200)


def _ref_to_dict(server_id: str, ref: MessageRef) -> dict:
    return {
        "slot": ref.slot,
        "label": ref.label or ref.slot,
        "channelId": ref.channel_id,
        "messageId": ref.message_id,
        "url": f"https://discord.com/channels/{server_id}/{ref.channel_id}/{ref.message_id}",
    }


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/servers/{server_id}/bot-messages")
    async def api_list_bot_messages(request: Request, server_id: str):
        await ctx.require_guild_admin(request, server_id)
        try:
            refs = await MessageRefRepository(server_id).list()
        except Exception as e:
            logger.exception("Listing the bot messages of %s failed: %s", server_id, e)
            raise HTTPException(
                status_code=500, detail="Erreur interne — détails dans les logs du bot."
            ) from e
        return JSONResponse({"messages": [_ref_to_dict(server_id, ref) for ref in refs]})

    @router.post("/api/servers/{server_id}/bot-messages/republish")
    async def api_republish_bot_message(request: Request, server_id: str, body: RepublishBody):
        session = await ctx.require_guild_admin(request, server_id)
        found = await ctx.call_bot(
            republish_persistent_message,
            ctx.bot,
            server_id,
            body.slot,
            logger=logger,
            what=f"republish bot message {body.slot} in {server_id}",
        )
        if not found:
            raise HTTPException(status_code=404, detail="Message introuvable")
        logger.info(
            "Bot message %s of guild %s republished (by %s/%s)",
            body.slot,
            server_id,
            session.username,
            session.user_id,
        )
        return JSONResponse({"republished": True})

    return router
