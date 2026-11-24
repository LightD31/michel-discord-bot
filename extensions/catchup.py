"""``/rattrapage``: the AI sums up what a channel said since your last message (P20).

Opt-in twice: the AI module on, and the channel listed by the admins in its
``catchupChannelIds``. The kill switch and the daily quotas of ``/ask``
apply (one call, counted for the member); the history goes to the provider
as ``/ask``'s does. Fewer than :data:`MIN_MESSAGES` to read costs no call.
The summary is the member's alone, its key moments linked; nothing is stored.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from interactions import Buckets, Client, Extension, SlashContext, cooldown, slash_command

from extensions.compareai._common import global_settings, guild_settings
from features.ai import AiClient, AiUsageRepository
from features.ai.catchup import (
    MAX_MESSAGES,
    MIN_MESSAGES,
    SUMMARY_TOKENS,
    ChannelMessage,
    prompt,
    since_last,
    with_links,
)
from features.ai.prompt import extract_response, replace_user_mentions
from features.ai.quota import QUOTA_UNAVAILABLE, access_refusal, limit_message
from features.ai.settings import resolve_default_model, usable_models
from src.core import logging as logutil
from src.core.config import ModuleConfig
from src.core.errors import DatabaseError
from src.discord_ext.look import card
from src.discord_ext.mentions import NO_PINGS
from src.discord_ext.messages import guild_id_of, guild_of, jump_url, send_error
from src.discord_ext.scopes import guild_scopes
from src.integrations import llm

logger = logutil.init_logger(__name__)

AI = ModuleConfig("moduleIA")
PAGE = 100  # Discord's most messages per fetch


def catchup_on(guild_id: str, channel: Any, channel_id: Any = None) -> bool:
    """Whether /rattrapage works in *channel*: listed itself, or a thread of a listed
    channel where the admins let threads follow their channel (P25)."""
    settings = AI.get(guild_id)
    listed = {str(c) for c in settings.get("catchupChannelIds") or []}
    if str(channel_id if channel_id is not None else getattr(channel, "id", "")) in listed:
        return True
    parent = getattr(channel, "parent_id", None)
    return bool(settings.get("catchupThreads")) and parent is not None and str(parent) in listed


def _names(guild: Any, content: str) -> str:
    """``<@id>`` → the member's name, from the cache (no API call)."""
    names = {}
    for member in list(getattr(guild, "members", None) or []):
        if f"<@{member.id}>" in content or f"<@!{member.id}>" in content:
            names[str(member.id)] = str(member.display_name)
    return replace_user_mentions(content, names) if names else content


class CatchupExtension(Extension):
    MODULE_KEYS = ("moduleIA",)
    HELP_LABEL = "📚 Rattrapage"

    def __init__(self, bot: Client):
        self.bot = bot
        self.ai_client = AiClient()

    async def history(self, channel: Any) -> list[Any]:
        """The channel's last :data:`MAX_MESSAGES` messages, oldest first."""
        found: list[Any] = []
        before = None
        while len(found) < MAX_MESSAGES:
            kwargs: dict[str, Any] = {"before": before} if before is not None else {}
            page = await channel.fetch_messages(
                limit=min(PAGE, MAX_MESSAGES - len(found)), **kwargs
            )
            if not page:
                break
            found.extend(page)
            before = page[-1].id
            if len(page) < PAGE:
                break
        return list(reversed(found))

    @slash_command(
        name="rattrapage",
        description="Ce qui s'est dit dans ce salon depuis ton dernier message, résumé par l'IA",
        scopes=guild_scopes(AI.guild_ids()),
    )
    @cooldown(Buckets.USER, 1, 60)
    async def rattrapage(self, ctx: SlashContext) -> None:
        guild_id = guild_id_of(ctx)
        if not catchup_on(guild_id, ctx.channel, ctx.channel_id):
            await send_error(
                ctx,
                "Le rattrapage n'est pas ouvert dans ce salon : un administrateur peut l'ajouter "
                "dans le module IA du tableau de bord de Michel (« Salons de /rattrapage »).",
            )
            return
        global_cfg = global_settings()
        refusal = access_refusal(global_cfg.limits, in_enabled_guild=True)
        if refusal:
            await send_error(ctx, refusal)
            return
        settings = guild_settings(guild_id)
        model = resolve_default_model(
            usable_models(global_cfg.models, llm.configured_apis(guild_id)),
            settings.default_model,
            global_cfg.default_model,
        )
        if model is None:
            await send_error(
                ctx,
                "IA non configurée sur ce serveur : un administrateur peut renseigner une clé OpenRouter ou NanoGPT dans le module IA du tableau de bord.",
            )
            return
        await ctx.defer(ephemeral=True)
        member_id = str(ctx.author.id)
        raw = await self.history(ctx.channel)
        messages = since_last(
            [
                ChannelMessage(
                    id=str(m.id),
                    author_id=str(m.author.id),
                    author=str(m.author.display_name),
                    content=_names(guild_of(ctx), m.content or ""),
                    is_bot=bool(getattr(m.author, "bot", False)),
                )
                for m in raw
                if getattr(m, "author", None) is not None
            ],
            member_id,
        )
        if len(messages) < MIN_MESSAGES:
            await ctx.send(
                f"📚 Rien à rattraper : {len(messages)} message{'s' if len(messages) > 1 else ''} "
                "depuis ton dernier. Lis-les directement !",
                ephemeral=True,
            )
            return
        try:
            hit = await AiUsageRepository().reserve(
                member_id, 1, global_cfg.limits, datetime.now(UTC)
            )
        except DatabaseError as e:
            logger.error("Catch-up quota check failed, refusing: %s", e)
            await send_error(ctx, QUOTA_UNAVAILABLE)
            return
        if hit:
            await send_error(ctx, limit_message(hit))
            return
        channel_name = str(getattr(ctx.channel, "name", "") or "ce salon")
        answer = await self.ai_client.complete(
            model,
            prompt(
                messages,
                persona=settings.persona,
                member=str(ctx.author.display_name),
                channel=channel_name,
                server=str(getattr(guild_of(ctx), "name", "") or "ce serveur"),
            ),
            SUMMARY_TOKENS,
            guild_id=guild_id,
        )
        if answer.error or not answer.content:
            logger.warning("Catch-up in guild %s failed: %s", guild_id, answer.error)
            await send_error(ctx, "L'IA n'a pas répondu cette fois : réessaie dans un instant.")
            return
        summary = with_links(
            extract_response(answer.content),
            messages,
            lambda message_id: jump_url(guild_id, ctx.channel_id, message_id),
        )
        embed = card(
            "moduleIA",
            title=f"📚 Rattrapage de #{channel_name}",
            description=summary,
            footer=f"{len(messages)} messages résumés · {model.display_name}",
        )
        await ctx.send(embeds=[embed], ephemeral=True, allowed_mentions=NO_PINGS)


def setup(bot: Client) -> None:
    CatchupExtension(bot)


__all__ = ["AI", "CatchupExtension", "catchup_on", "setup"]
