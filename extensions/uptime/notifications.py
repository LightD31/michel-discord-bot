"""Turn Kuma heartbeats into status-change embeds in each guild's channel."""

from datetime import UTC, datetime

from interactions import Embed, MessageableMixin

from features.uptime import (
    AlertEvent,
    AlertMessage,
    AlertTone,
    MonitorAlert,
    UptimeRepository,
    render_alert,
    transition,
)
from src.core import logging as logutil
from src.core.errors import DatabaseError
from src.discord_ext.embeds import Colors
from src.integrations.uptime_kuma import Heartbeat, MonitorStatus

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
    """Apply each beat to every guild watching its monitor."""

    async def handle_heartbeat(self, beat: Heartbeat) -> None:
        async with self._state_lock:
            for guild_id, alerts in self.alerts.items():
                alert = alerts.get(beat.monitor_id)
                if alert is None:
                    continue
                change = transition(alert.last_status, beat.status)
                previous = alert.last_status
                if change.status is not previous:
                    alert.last_status = change.status
                    try:
                        await UptimeRepository(guild_id).set_last_status(
                            alert.monitor_id, change.status
                        )
                    except DatabaseError as e:
                        logger.error(
                            "Guild %s: could not save status of monitor %s: %s",
                            guild_id,
                            alert.monitor_id,
                            e,
                        )
                if change.event is not None:
                    await self._post_alert(guild_id, alert, change.event, previous, beat)

    async def _post_alert(
        self,
        guild_id: str,
        alert: MonitorAlert,
        event: AlertEvent,
        previous: MonitorStatus | None,
        beat: Heartbeat,
    ) -> None:
        channel = self.bot.get_channel(alert.channel_id)
        if not isinstance(channel, MessageableMixin):
            logger.warning(
                "Guild %s: uptime channel %s missing or not messageable, monitor %s alert dropped",
                guild_id,
                alert.channel_id,
                alert.monitor_id,
            )
            return

        monitor = self.kuma.monitors.get(alert.monitor_id)
        message = render_alert(
            event,
            alert.mode,
            monitor_id=alert.monitor_id,
            monitor_name=monitor.display_name if monitor else f"ID {alert.monitor_id}",
            previous=previous,
            current=beat.status,
            url=monitor.url if monitor else None,
            message=beat.msg,
            ping=beat.ping,
            at=beat.time or datetime.now(UTC),
        )
        try:
            await channel.send(embed=_to_embed(message))
        except Exception as e:
            logger.error(
                "Guild %s: could not post the alert for monitor %s: %s",
                guild_id,
                alert.monitor_id,
                e,
            )
            return
        logger.info(
            "Guild %s: monitor %s %s → %s posted (%s)",
            guild_id,
            alert.monitor_id,
            previous.name if previous is not None else None,
            beat.status.name,
            alert.mode.value,
        )
