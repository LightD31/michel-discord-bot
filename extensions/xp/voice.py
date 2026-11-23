"""VoiceMixin — award XP for time spent in voice channels.

Sessions are tracked in memory keyed by ``(guild_id, user_id)`` with the
timestamp of the last award. Every ``VOICE_TICK_SECONDS`` the periodic task
sweeps tracked sessions and awards a small XP amount, which keeps the database
write rate bounded and naturally drops users who disconnect (their session is
removed on the leave event).

XP is only awarded when:
- the channel is not the guild's AFK channel, and
- the channel has at least one other non-bot member (to avoid solo farming).
"""

import random
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from interactions import Client, IntervalTrigger, Task, listen
from interactions.api.events import VoiceStateUpdate

from features.xp import (
    VOICE_TICK_SECONDS,
    VOICE_XP_PER_TICK_MAX,
    VOICE_XP_PER_TICK_MIN,
    TTLCache,
    XpRepository,
    calculate_level,
)
from src.core import log_context
from src.core.errors import DatabaseError

from ._common import XP, logger


class VoiceMixin:
    """Award XP per minute for active voice-channel presence."""

    bot: Client
    _rank_cache: TTLCache
    _voice_sessions: dict[tuple[str, str], float]  # (guild_id, user_id) -> last-award ts

    if TYPE_CHECKING:  # LevelingMixin's

        def _repo(self, guild_id: str) -> XpRepository: ...
        def _invalidate_rank_cache(self, guild_id: str) -> None: ...
        async def _handle_level_up(
            self, guild_id: str, user_id: str, new_level: int, message
        ) -> None: ...
        async def _announce_level_up(self, guild_id: str, member, new_level: int, here) -> None: ...

    @listen()
    async def on_voice_state_update(self, event: VoiceStateUpdate):
        before = event.before
        after = event.after
        state = after or before
        member = state.member if state else None
        if member is None or member.bot:
            return
        guild = member.guild
        if guild is None or not XP.enabled(guild.id):
            return
        guild_id = str(guild.id)
        if not XP.get(guild_id).get("voiceXpEnabled", False):
            return

        key = (guild_id, str(member.id))
        before_chan = before.channel if before else None
        after_chan = after.channel if after else None

        if after_chan is None and before_chan is not None:
            self._voice_sessions.pop(key, None)
        elif after_chan is not None and before_chan is None:
            self._voice_sessions[key] = time.time()
        # Channel switches (move) keep the session running.

    def _resume_voice_sessions(self) -> int:
        """Track the members already in a voice channel; returns how many.

        Sessions start on join events, and the joins that happened before a
        restart or a reload are gone: whoever was in voice then earned nothing
        until they left and came back. Called from ``on_startup``.
        """
        now = time.time()
        resumed = 0
        for guild_id in XP.guild_ids():
            if not XP.get(guild_id).get("voiceXpEnabled", False):
                continue
            guild = self.bot.get_guild(int(guild_id))
            if guild is None:
                continue
            for state in guild.voice_states:
                member = state.member
                if state.channel is None or member is None or member.bot:
                    continue
                key = (str(guild_id), str(member.id))
                if key not in self._voice_sessions:
                    self._voice_sessions[key] = now
                    resumed += 1
        return resumed

    @Task.create(IntervalTrigger(seconds=VOICE_TICK_SECONDS))
    async def _voice_xp_tick(self):
        now = time.time()
        for key in list(self._voice_sessions.keys()):
            with log_context.scope(guild_id=key[0]):
                await self._tick_voice_session(key, now)

    async def _tick_voice_session(self, key: tuple[str, str], now: float) -> None:
        guild_id, user_id = key
        last = self._voice_sessions.get(key)
        if last is None or now - last < VOICE_TICK_SECONDS:
            return

        guild = self.bot.get_guild(int(guild_id))
        if guild is None:
            self._voice_sessions.pop(key, None)
            return
        member = guild.get_member(int(user_id))
        if member is None or member.voice is None or member.voice.channel is None:
            self._voice_sessions.pop(key, None)
            return

        voice_channel = member.voice.channel
        afk_channel_id = getattr(guild, "afk_channel_id", None)
        if afk_channel_id and int(voice_channel.id) == int(afk_channel_id):
            return  # don't reward AFK lurkers

        # voice_members may be a property exposing current channel members; fall
        # back to scanning all guild members if not present (older library).
        members = getattr(voice_channel, "voice_members", None)
        if members is None:
            members = [m for m in guild.members if _voice_channel_id(m) == voice_channel.id]
        non_bot_count = sum(1 for m in members if not getattr(m, "bot", False))
        if non_bot_count < 2:
            return  # solo in channel; don't award

        xp_gained = random.randint(VOICE_XP_PER_TICK_MIN, VOICE_XP_PER_TICK_MAX)
        repo = self._repo(guild_id)
        try:
            stats = await repo.get_user(user_id)
            if stats is None:
                await repo.insert_new_user(user_id, xp_gained, now)
                self._invalidate_rank_cache(guild_id)
                self._voice_sessions[key] = now
                return
            # Message count is untouched here — a voice tick is not a message.
            updated = await repo.award_xp(user_id, xp_gained, 0, now)
            self._invalidate_rank_cache(guild_id)
        except DatabaseError as e:
            logger.error("Voice XP DB error for %s in %s: %s", user_id, guild_id, e)
            return

        self._voice_sessions[key] = now

        if updated is None:
            return

        try:
            await repo.log_event(
                user_id,
                xp_gained,
                updated.get("xp", 0),
                datetime.fromtimestamp(now, UTC),
                source="voice",
            )
        except DatabaseError as e:
            logger.warning("Failed to log voice XP event for %s: %s", user_id, e)

        # Post-increment totals from the database, so a message award landing
        # in the same tick is counted rather than overwritten.
        new_level, _, _ = calculate_level(updated.get("xp", 0))
        old_level = updated.get("lvl", 0)
        if new_level > old_level:
            # We don't have a "message" object here; pass the voice channel so
            # the level-up handler can announce in the user's current channel.
            await self._handle_voice_level_up(guild_id, user_id, new_level, voice_channel, member)

    async def _handle_voice_level_up(
        self, guild_id: str, user_id: str, new_level: int, voice_channel, member
    ) -> None:
        """Persist the new level and announce in a sensible place."""
        await self._repo(guild_id).set_level(user_id, new_level)
        # "Là où le membre parlait": the chat of their voice channel.
        await self._announce_level_up(guild_id, member, new_level, voice_channel)
        await self._apply_level_rewards(guild_id, member, new_level)  # type: ignore[attr-defined]


def _voice_channel_id(member: Any) -> Any:
    """The id of the voice channel *member* is in, ``None`` when in none."""
    channel = getattr(getattr(member, "voice", None), "channel", None)
    return getattr(channel, "id", None)
