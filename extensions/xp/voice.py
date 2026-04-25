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

import pymongo
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
from src.discord_ext.autocomplete import is_guild_enabled

from ._common import enabled_servers, logger, module_config


class VoiceMixin:
    """Award XP per minute for active voice-channel presence."""

    bot: Client
    _db_connected: bool
    _rank_cache: TTLCache
    _repos: dict[str, XpRepository]
    _voice_sessions: dict[tuple[str, str], float]  # (guild_id, user_id) -> last-award ts

    def _repo(self, guild_id: str) -> XpRepository: ...  # provided by LevelingMixin
    def _invalidate_rank_cache(self, guild_id: str) -> None: ...  # provided by LevelingMixin

    async def _handle_level_up(
        self, guild_id: str, user_id: str, new_level: int, message
    ) -> None: ...  # provided by LevelingMixin

    @listen()
    async def on_voice_state_update(self, event: VoiceStateUpdate):
        before = event.before
        after = event.after
        member = (after or before).member if (after or before) else None
        if member is None or member.bot:
            return
        guild = member.guild
        if guild is None or not is_guild_enabled(guild.id, enabled_servers):
            return
        guild_id = str(guild.id)
        if not module_config.get(guild_id, {}).get("voiceXpEnabled", False):
            return

        key = (guild_id, str(member.id))
        before_chan = before.channel if before else None
        after_chan = after.channel if after else None

        if after_chan is None and before_chan is not None:
            self._voice_sessions.pop(key, None)
        elif after_chan is not None and before_chan is None:
            self._voice_sessions[key] = time.time()
        # Channel switches (move) keep the session running.

    @Task.create(IntervalTrigger(seconds=VOICE_TICK_SECONDS))
    async def _voice_xp_tick(self):
        if not getattr(self, "_db_connected", False):
            return
        now = time.time()
        for key in list(self._voice_sessions.keys()):
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
            members = [
                m
                for m in guild.members
                if getattr(m, "voice", None)
                and getattr(m.voice, "channel", None)
                and m.voice.channel.id == voice_channel.id
            ]
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
            new_xp = stats.get("xp", 0) + xp_gained
            new_msg = stats.get("msg", 0)
            await repo.update_xp(user_id, new_xp, new_msg, now)
            self._invalidate_rank_cache(guild_id)
        except pymongo.errors.PyMongoError as e:
            logger.error("Voice XP DB error for %s in %s: %s", user_id, guild_id, e)
            return

        self._voice_sessions[key] = now

        new_level, _, _ = calculate_level(new_xp)
        old_level = stats.get("lvl", 0)
        if new_level > old_level:
            # We don't have a "message" object here; pass the voice channel so
            # the level-up handler can announce in the user's current channel.
            await self._handle_voice_level_up(guild_id, user_id, new_level, voice_channel, member)

    async def _handle_voice_level_up(
        self, guild_id: str, user_id: str, new_level: int, voice_channel, member
    ) -> None:
        """Persist the new level and announce in a sensible place."""
        await self._repo(guild_id).set_level(user_id, new_level)
        announce = getattr(voice_channel, "send", None)
        if announce is not None:
            try:
                await announce(
                    f"Bravo {member.mention}, tu as atteint le niveau {new_level} (vocal) !"
                )
            except Exception as e:
                logger.debug("Could not announce voice level-up: %s", e)
        # Role rewards are applied by LevelingMixin via a shared helper resolved
        # through the host class's MRO.
        await self._apply_level_rewards(guild_id, member, new_level)  # type: ignore[attr-defined]
