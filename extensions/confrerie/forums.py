"""Turn forum activity into drafts: new posts in « textes », answers in « défis ».

The owner used to copy every forum post into Notion by hand; now each one
arrives as a pre-filled draft to approve (see ``review.py``). Both forums are
opt-in through their config field.
"""

import os

from interactions import listen
from interactions.api.events import MessageCreate, NewThreadCreate

from features.confrerie import notion_props as np
from features.confrerie.drafts import (
    SOURCE_DEFI,
    SOURCE_TEXTES,
    Draft,
    defi_draft_id,
    looks_like_answer,
    match_defi,
    resolve_author,
    tags_to_options,
    textes_draft_id,
    title_from_message,
)
from features.confrerie.stats import COL_DEFI, COL_GENRE, COL_TYPE
from src.core import logging as logutil

from ._common import CONFRERIE

logger = logutil.init_logger(__name__)


def _thread_url(guild: object, thread_id: object) -> str:
    return f"https://discord.com/channels/{guild}/{thread_id}"


def _guild_id_of(obj: object) -> str:
    """The guild id of a channel or message, as a string ("" outside a guild)."""
    guild_id = getattr(obj, "_guild_id", None)
    if guild_id is None:
        guild = getattr(obj, "guild", None)
        guild_id = getattr(guild, "id", None)
    return str(guild_id) if guild_id else ""


class ForumsMixin:
    """Listen to the configured forums and submit drafts for review."""

    def _author_map(self, settings: dict) -> dict[str, str]:
        return settings.get("confrerieAuthorMap") or {}

    async def _display_name(self, guild, user_id) -> str:
        """Member's display name; the raw id when they can't be fetched (left, API error)."""
        if guild is None:
            return str(user_id)
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except Exception:  # noqa: BLE001 — fall back to the id, keep the draft
                member = None
        return member.display_name if member else str(user_id)

    @listen(NewThreadCreate)
    async def on_textes_post(self, event: NewThreadCreate):
        thread = event.thread
        guild_id = _guild_id_of(thread)
        settings = CONFRERIE.get(guild_id)
        forum_id = settings.get("confrerieTextesForumId")
        if not forum_id or str(getattr(thread, "parent_id", "")) != str(forum_id):
            return
        owner_id = getattr(thread, "owner_id", None)
        if not owner_id or int(owner_id) == int(self.bot.user.id):
            return
        try:
            schema = await self._oeuvres_schema(settings)
            try:
                tags = [tag.name for tag in thread.applied_tags]
            except AttributeError:  # parent forum not cached
                tags = []

            display_name = await self._display_name(thread.guild, owner_id)
            author, mapped = resolve_author(int(owner_id), display_name, self._author_map(settings))

            draft = Draft(
                id=textes_draft_id(int(thread.id)),
                source=SOURCE_TEXTES,
                guild_id=guild_id,
                submitter_id=int(owner_id),
                title=thread.name[:100],
                authors=[author],
                types=tags_to_options(tags, np.schema_options(schema, COL_TYPE)),
                genres=tags_to_options(tags, np.schema_options(schema, COL_GENRE)),
                link=_thread_url(guild_id, thread.id),
                link_label="Fil Discord",
                channel_id=int(thread.id),
                unmapped_author=not mapped,
            )
            await self.submit_draft(draft)
        except Exception as e:  # noqa: BLE001 — a listener must never crash the gateway
            logger.error("Fiche du fil %s non créée: %s", thread.id, e)

    @listen(MessageCreate)
    async def on_defi_answer(self, event: MessageCreate):
        message = event.message
        guild_id = _guild_id_of(message)
        settings = CONFRERIE.get(guild_id)
        forum_id = settings.get("confrerieDefisForumId")
        if not forum_id or message.author.bot:
            return
        channel = message.channel
        if channel is None or str(getattr(channel, "parent_id", "")) != str(forum_id):
            return
        # A forum post's opening message shares the thread's id: that's the défi
        # prompt itself, not an answer.
        if int(message.id) == int(channel.id):
            return
        if not looks_like_answer(message.content or "", has_attachment=bool(message.attachments)):
            return
        try:
            repo = self._drafts(guild_id)
            draft_id = defi_draft_id(int(channel.id), int(message.author.id))
            if await repo.get(draft_id):
                return  # this member already answered this défi

            schema = await self._oeuvres_schema(settings)
            defi = match_defi(channel.name, np.schema_options(schema, COL_DEFI))
            author, mapped = resolve_author(
                int(message.author.id), message.author.display_name, self._author_map(settings)
            )
            type_defi = np.canonical_option("Défi", np.schema_options(schema, COL_TYPE))
            draft = Draft(
                id=draft_id,
                source=SOURCE_DEFI,
                guild_id=guild_id,
                submitter_id=int(message.author.id),
                title=title_from_message(
                    message.content or "", f"{defi or channel.name} — {author}"
                ),
                authors=[author],
                types=[type_defi or "Défi"],
                defi=defi,
                link=message.jump_url,
                link_label="Réponse Discord",
                channel_id=int(channel.id),
                message_id=int(message.id),
                unmapped_author=not mapped,
            )
            await self.submit_draft(draft)
        except Exception as e:  # noqa: BLE001 — a listener must never crash the gateway
            logger.error("Fiche de défi non créée pour le message %s: %s", message.id, e)
