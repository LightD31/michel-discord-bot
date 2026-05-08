"""NotificationsMixin — scheduled tasks and match transition orchestration."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from interactions import IntervalTrigger, Task

from features.mdi import MatchSnapshot

from ._common import (
    LIVE_INTERVAL_MINUTES,
    SCHEDULE_INTERVAL_MINUTES,
    GuildState,
    logger,
    save_schedule_channel_message_id,
)

if TYPE_CHECKING:  # pragma: no cover
    from interactions import Client

# Slow-tick guard: when no bracket is currently active, only do real work every
# this-many seconds even if the slow-task fires more often.
_INACTIVE_POLL_INTERVAL_SECONDS = 1800.0  # 30 min


def _event_url(event_slug: str) -> str:
    return f"https://raider.io/events/{event_slug}"


class NotificationsMixin:
    """Mixin: tasks + per-match diff/transition logic."""

    # Attributes provided by the Extension class composition. Declared here so
    # mypy doesn't complain about ``self.bot`` / ``self._servers`` accesses in
    # this file.
    bot: Client
    _servers: dict[str, GuildState]
    _last_inactive_run: dict[str, float]

    # ── Scheduled tasks ──────────────────────────────────────────────────────

    @Task.create(IntervalTrigger(minutes=SCHEDULE_INTERVAL_MINUTES))
    async def schedule(self) -> None:
        """Slow ticker — refreshes the pinned schedule embed periodically."""
        for server_id, state in list(self._servers.items()):
            try:
                await self._run_cycle(state, force=True)
            except Exception as e:
                logger.exception("MDI schedule cycle failed for guild %s: %s", server_id, e)

    @Task.create(IntervalTrigger(minutes=LIVE_INTERVAL_MINUTES))
    async def live_update(self) -> None:
        """Fast ticker — runs frequently when a bracket is in its window."""
        for server_id, state in list(self._servers.items()):
            try:
                await self._run_cycle(state, force=False)
            except Exception as e:
                logger.exception("MDI live cycle failed for guild %s: %s", server_id, e)

    # ── Cycle ────────────────────────────────────────────────────────────────

    async def _run_cycle(self, state: GuildState, *, force: bool) -> None:
        """Single tracking cycle for one guild.

        ``force=True`` ignores the inactive-window throttle. ``force=False``
        only runs when at least one tracked match is in an active bracket
        window or the throttle interval has elapsed since the last full run.
        """
        if state.notification_channel is None:
            return  # nothing wired up

        team_ref, matches, brackets = await self._collect_team_matches(state.guild_config)
        if state.tracked_team is None and team_ref is not None:
            state.tracked_team = team_ref

        if not force:
            active = self._has_active_bracket(brackets, matches)
            now = time.monotonic()
            last = self._last_inactive_run.get(state.server_id, 0.0)
            if not active and (now - last) < _INACTIVE_POLL_INTERVAL_SECONDS:
                return
            self._last_inactive_run[state.server_id] = now

        # Per-match processing — post / edit / finalise
        for match in matches:
            try:
                await self._process_match(state, match)
            except Exception as e:
                logger.exception(
                    "MDI: failed to process match %s for guild %s: %s",
                    match.id,
                    state.server_id,
                    e,
                )

        # Schedule embed
        try:
            await self._refresh_schedule_message(state, matches)
        except Exception as e:
            logger.exception(
                "MDI: failed to refresh schedule message for guild %s: %s", state.server_id, e
            )

    # ── Per-match handler ────────────────────────────────────────────────────

    async def _process_match(self, state: GuildState, match: MatchSnapshot) -> None:
        team = state.tracked_team
        gc = state.guild_config
        channel = state.notification_channel
        if channel is None:
            return

        new_hash = self._match_hash(match)
        phase = self._match_phase(match)
        doc = state.matches.get(match.id)
        event_url = _event_url(gc.event_slug)
        embed = self._build_match_embed(match, team, event_url)

        if doc is None:
            # First time — post a fresh message
            content = self._initial_post_content(state, phase)
            try:
                msg = await channel.send(content=content, embeds=[embed])
            except Exception as e:
                logger.warning(
                    "MDI: could not post match %s in guild %s: %s",
                    match.id,
                    state.server_id,
                    e,
                )
                return
            await self._persist_match_doc(
                state,
                match=match,
                channel_id=str(channel.id),
                message_id=str(msg.id),
                last_hash=new_hash,
                terminal=match.is_terminal,
                notified_live=(phase != "scheduled"),
            )
            logger.info(
                "MDI: posted match %s for guild %s (phase=%s)",
                match.id,
                state.server_id,
                phase,
            )
            return

        # Already known — short-circuit for terminal matches we've already finalised
        if doc.get("terminal") is True and match.is_terminal:
            return

        if doc.get("last_hash") == new_hash and bool(doc.get("terminal")) == match.is_terminal:
            return

        # Edit the existing message in place
        message_id = doc.get("message_id")
        channel_id = doc.get("channel_id") or str(channel.id)
        msg = await self._fetch_message_safe(channel_id, message_id)
        if msg is None:
            # Stale — repost
            try:
                msg = await channel.send(embeds=[embed])
            except Exception as e:
                logger.warning(
                    "MDI: could not repost stale match %s in guild %s: %s",
                    match.id,
                    state.server_id,
                    e,
                )
                return
            channel_id = str(channel.id)
        else:
            try:
                await msg.edit(embeds=[embed])
            except Exception as e:
                logger.warning(
                    "MDI: could not edit match %s in guild %s: %s", match.id, state.server_id, e
                )
                return

        # Separate live ping if we just transitioned out of scheduled phase
        notified = bool(doc.get("notified_live", False))
        if not notified and phase != "scheduled" and gc.ping_role_id and not match.is_terminal:
            try:
                first_name = match.first_team.name if match.first_team else "TBD"
                second_name = match.second_team.name if match.second_team else "TBD"
                await channel.send(
                    content=(
                        f"<@&{gc.ping_role_id}> 🔴 **{first_name} vs {second_name}** est en direct !"
                    )
                )
                notified = True
            except Exception as e:
                logger.warning(
                    "MDI: could not post live notification for match %s: %s", match.id, e
                )

        await self._persist_match_doc(
            state,
            match=match,
            channel_id=channel_id,
            message_id=str(msg.id),
            last_hash=new_hash,
            terminal=match.is_terminal,
            notified_live=notified or phase != "scheduled",
        )

    @staticmethod
    def _initial_post_content(state: GuildState, phase: str) -> str | None:
        role_id = state.guild_config.ping_role_id
        if role_id and phase == "live":
            return f"<@&{role_id}> 🔴 Match en direct"
        return None

    async def _persist_match_doc(
        self,
        state: GuildState,
        *,
        match: MatchSnapshot,
        channel_id: str,
        message_id: str,
        last_hash: str,
        terminal: bool,
        notified_live: bool,
    ) -> None:
        from datetime import UTC, datetime

        doc: dict[str, Any] = {
            "_id": f"match:{match.id}",
            "match_id": match.id,
            "bracket_slug": match.bracket_slug,
            "channel_id": channel_id,
            "message_id": message_id,
            "last_hash": last_hash,
            "terminal": terminal,
            "notified_live": notified_live,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        try:
            await self._matches_col(state.server_id).replace_one(
                {"_id": doc["_id"]}, doc, upsert=True
            )
        except Exception as e:
            logger.warning(
                "MDI: could not persist match %s for guild %s: %s",
                match.id,
                state.server_id,
                e,
            )
            return
        state.matches[match.id] = doc

    # ── Schedule message ─────────────────────────────────────────────────────

    async def _refresh_schedule_message(
        self, state: GuildState, matches: list[MatchSnapshot]
    ) -> None:
        gc = state.guild_config
        channel = state.notification_channel
        if channel is None:
            return

        embed = self._build_schedule_embed(state.tracked_team, gc.team_slug, matches)
        new_hash = self._schedule_hash(matches)

        # Try to use the existing message
        if state.schedule_message is not None:
            if state.schedule_last_hash == new_hash:
                return
            try:
                await state.schedule_message.edit(embeds=[embed])
                state.schedule_last_hash = new_hash
                return
            except Exception as e:
                logger.warning(
                    "MDI: schedule message edit failed in guild %s, will repost: %s",
                    state.server_id,
                    e,
                )
                state.schedule_message = None

        # Need to (re)create
        try:
            msg = await channel.send(embeds=[embed])
        except Exception as e:
            logger.warning(
                "MDI: could not post schedule message in guild %s: %s", state.server_id, e
            )
            return
        if gc.pin_schedule:
            try:
                await msg.pin()
            except Exception as e:
                logger.debug("MDI: could not pin schedule message: %s", e)

        state.schedule_message = msg
        state.schedule_last_hash = new_hash
        save_schedule_channel_message_id(state.server_id, str(channel.id), str(msg.id))

    # ── Helpers ──────────────────────────────────────────────────────────────

    async def _fetch_message_safe(self, channel_id: str | None, message_id: Any) -> Any:
        """Fetch a message by (channel_id, message_id). Returns ``None`` on failure."""
        if not channel_id or not message_id:
            return None
        try:
            channel = await self.bot.fetch_channel(channel_id)
        except Exception as e:
            logger.debug("MDI: fetch_channel(%s) failed: %s", channel_id, e)
            return None
        if channel is None or not hasattr(channel, "fetch_message"):
            return None
        try:
            return await channel.fetch_message(message_id)
        except Exception as e:
            logger.debug("MDI: fetch_message(%s) failed: %s", message_id, e)
            return None
