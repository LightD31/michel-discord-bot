"""Turn status changes read from Kuma into embeds in each guild's channel."""

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from interactions import Client, Embed, MessageableMixin

from features.uptime import (
    AlertMessage,
    AlertTone,
    MonitorAlert,
    StatusChange,
    UptimeRepository,
    apply_statuses,
    render_alert,
)
from src.core import logging as logutil
from src.core.errors import DatabaseError
from src.core.logging import log_failure
from src.discord_ext.channels import fetch_guild_channel
from src.discord_ext.look import Kind, card
from src.integrations.uptime_kuma import MonitorState, MonitorStatus

logger = logutil.init_logger(__name__)

_TONE_KINDS: dict[AlertTone, Kind] = {
    AlertTone.DOWN: "error",
    AlertTone.MAINTENANCE: "warning",
    AlertTone.UP: "success",
}


def _to_embed(message: AlertMessage) -> Embed:
    embed = card(
        "moduleUptime",
        _TONE_KINDS[message.tone],
        title=message.title,
        description=message.description,
        timestamp=message.timestamp,
    )
    for field in message.fields:
        embed.add_field(name=field.name, value=field.value, inline=field.inline)
    return embed


class NotificationsMixin:
    """Apply each guild's /metrics read to its alerts."""

    # Set up by the extension (__init__.py).
    bot: Client
    statuses: dict[str, dict[int, MonitorStatus | None]]
    _state_lock: asyncio.Lock

    if TYPE_CHECKING:

        def alerts_of(self, guild_id: str) -> dict[int, MonitorAlert]: ...

    async def apply_states(self, guild_id: str, states: dict[int, MonitorState]) -> None:
        """Advance the guild's alerts with a read of its own Kuma."""
        async with self._state_lock:
            changes = apply_statuses(
                {guild_id: self.alerts_of(guild_id)},
                {i: s.status for i, s in states.items()},
            )
            for change in changes:
                seen = self.statuses.setdefault(change.guild_id, {})
                seen[change.alert.monitor_id] = change.alert.last_status
                try:
                    await UptimeRepository(change.guild_id).set_last_status(
                        change.alert.monitor_id, change.alert.last_status
                    )
                except DatabaseError as e:
                    logger.error(
                        "Guild %s: could not save status of monitor %s: %s",
                        change.guild_id,
                        change.alert.monitor_id,
                        e,
                    )
                if change.event is not None:
                    await self._post_alert(change, states[change.alert.monitor_id])

    async def _post_alert(self, change: StatusChange, state: MonitorState) -> None:
        event = change.event
        if event is None:  # a status saved without an alert to post
            return
        alert = change.alert
        channel = await fetch_guild_channel(self.bot, change.guild_id, alert.channel_id)
        if not isinstance(channel, MessageableMixin):
            logger.warning(
                "Guild %s: uptime channel %s missing or not messageable, monitor %s alert dropped",
                change.guild_id,
                alert.channel_id,
                alert.monitor_id,
            )
            return

        message = render_alert(
            event,
            alert.mode,
            monitor_id=alert.monitor_id,
            monitor_name=state.name,
            previous=change.previous,
            current=state.status,
            url=state.url,
            ping=state.response_time_ms,
            at=datetime.now(UTC),
        )
        try:
            await channel.send(embed=_to_embed(message))
        except Exception as e:
            log_failure(
                logger,
                "Guild %s: could not post the alert for monitor %s: %s",
                change.guild_id,
                alert.monitor_id,
                e,
            )
            return
        logger.info(
            "Guild %s: monitor %s %s → %s posted (%s)",
            change.guild_id,
            alert.monitor_id,
            change.previous.name if change.previous is not None else None,
            state.status.name,
            alert.mode.value,
        )
