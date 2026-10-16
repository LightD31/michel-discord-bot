"""Turn status changes read from Kuma into embeds in each guild's channel."""

from datetime import UTC, datetime

from interactions import Embed, MessageableMixin

from features.uptime import (
    AlertMessage,
    AlertTone,
    StatusChange,
    UptimeRepository,
    apply_statuses,
    render_alert,
)
from src.core import logging as logutil
from src.core.errors import DatabaseError
from src.discord_ext.embeds import Colors
from src.integrations.uptime_kuma import MonitorState

logger = logutil.init_logger(__name__)

_TONE_COLORS = {
    AlertTone.DOWN: Colors.ERROR,
    AlertTone.MAINTENANCE: Colors.ORANGE,
    AlertTone.UP: Colors.SUCCESS,
}


def _to_embed(message: AlertMessage) -> Embed:
    embed = Embed(
        title=message.title,
        description=message.description,
        color=_TONE_COLORS[message.tone],
        timestamp=message.timestamp,
    )
    for field in message.fields:
        embed.add_field(name=field.name, value=field.value, inline=field.inline)
    return embed


class NotificationsMixin:
    """Apply each /metrics read to every guild's alerts."""

    async def apply_states(self, states: dict[int, MonitorState]) -> None:
        async with self._state_lock:
            changes = apply_statuses(self.alerts, {i: s.status for i, s in states.items()})
            for change in changes:
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
        alert = change.alert
        channel = self.bot.get_channel(alert.channel_id)
        if not isinstance(channel, MessageableMixin):
            logger.warning(
                "Guild %s: uptime channel %s missing or not messageable, monitor %s alert dropped",
                change.guild_id,
                alert.channel_id,
                alert.monitor_id,
            )
            return

        message = render_alert(
            change.event,
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
            logger.error(
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
