"""Automatic message filtering (AutomodMixin).

A single :class:`MessageCreate` listener enforces three independently-toggleable
rules — anti-invite, banned-words, anti-spam — with channel/role/staff
exemptions. A trigger deletes the message, records an ``automod`` infraction,
mirrors it to the modlog, and DMs the author (when enabled).
"""

import time
from collections import deque
from datetime import datetime
from typing import Any

from interactions import listen
from interactions.api.events import MessageCreate

from features.moderation import Infraction, contains_invite, match_banned_word

from ._common import AUTOMOD_MODERATOR_ID, get_guild_settings, is_staff, logger


class AutomodMixin:
    """MessageCreate-driven invite/spam/word filtering."""

    # Per-(guild, user) sliding window of message timestamps for anti-spam.
    _spam: dict[tuple[str, str], deque]

    @listen(MessageCreate)
    async def on_message_automod(self, event: MessageCreate) -> None:
        try:
            await self._run_automod(event)
        except Exception as e:  # never let automod crash the event loop
            logger.warning("automod error: %s", e)

    async def _run_automod(self, event: MessageCreate) -> None:
        msg = event.message
        guild = getattr(msg, "guild", None)
        if guild is None:
            return
        author = msg.author
        if author is None or getattr(author, "bot", False):
            return

        settings = get_guild_settings(guild.id)
        if not settings:  # module_config only holds enabled guilds
            return
        if is_staff(author, settings):
            return

        ignored_channels = {str(c) for c in settings.get("ignoredChannelIds", []) or []}
        if str(msg.channel.id) in ignored_channels:
            return
        ignored_roles = {str(r) for r in settings.get("ignoredRoleIds", []) or []}
        if ignored_roles:
            member_role_ids = {str(getattr(r, "id", r)) for r in getattr(author, "roles", []) or []}
            if member_role_ids & ignored_roles:
                return

        content = msg.content or ""

        if settings.get("antiInvite") and contains_invite(content):
            await self._punish(msg, settings, "Invitation Discord interdite")
            return

        banned = settings.get("bannedWords") or []
        if banned:
            hit = match_banned_word(content, banned)
            if hit:
                await self._punish(msg, settings, f"Mot interdit : {hit}")
                return

        if settings.get("antiSpam") and self._track_spam(msg, settings):
            threshold = settings.get("spamThreshold", 5)
            window = settings.get("spamWindowSeconds", 7)
            await self._punish(msg, settings, f"Spam ({threshold} messages / {window}s)")

    def _track_spam(self, msg: Any, settings: dict) -> bool:
        threshold = int(settings.get("spamThreshold", 5) or 5)
        window = float(settings.get("spamWindowSeconds", 7) or 7)
        key = (str(msg.guild.id), str(msg.author.id))
        now = time.monotonic()
        dq = self._spam.setdefault(key, deque())
        dq.append(now)
        while dq and now - dq[0] > window:
            dq.popleft()
        if len(dq) >= threshold:
            dq.clear()  # reset so we don't re-trigger on every subsequent message
            return True
        return False

    async def _punish(self, msg: Any, settings: dict, reason: str) -> None:
        try:
            await msg.delete()
        except Exception as e:
            logger.debug("automod could not delete message: %s", e)

        try:
            repo = self.repository(msg.guild.id)
            case_id = await repo.next_case_id()
            infraction = Infraction(
                id=case_id,
                guild_id=str(msg.guild.id),
                user_id=str(msg.author.id),
                moderator_id=AUTOMOD_MODERATOR_ID,
                type="automod",
                reason=reason,
                source="automod",
                created_at=datetime.now(),
            )
            await repo.add(infraction)
            await self.log_case(settings, infraction, target_name=msg.author.mention)
            await self.dm_target(
                settings,
                msg.author,
                f"🛡️ Ton message sur **{msg.guild.name}** a été supprimé.\nRaison : {reason}",
            )
        except Exception as e:
            logger.warning("automod could not record action: %s", e)
