"""EventsMixin — Zunivers event tracking and hardcore season notifications."""

from interactions import (
    GuildText,
    OrTrigger,
    SlashContext,
    Task,
    TimeTrigger,
    slash_command,
)

from features.coloc.constants import ReminderType
from features.coloc.models import HardcoreSeason, ZuniversEvent
from features.coloc.utils import (
    create_event_embed,
    create_season_embed,
    image_url_needs_download,
    set_event_embed_image,
)
from src.discord_ext.messages import send_error

from ._common import enabled_servers, logger


class EventsMixin:
    """Hourly Zunivers event polling + hardcore season state transitions."""

    @Task.create(OrTrigger(*[TimeTrigger(hour=i, minute=1) for i in range(24)]))
    async def events_checker(self):
        """Check for Zunivers event changes every hour."""
        await self._check_zunivers_events()

    @slash_command(
        name="zunivers",
        sub_cmd_name="check",
        sub_cmd_description="Vérifie manuellement les événements Zunivers",
        description="Gère les événements Zunivers",
        scopes=enabled_servers,
    )
    async def zunivers_check(self, ctx: SlashContext):
        """Manually trigger event check."""
        await ctx.defer()
        try:
            await self._check_zunivers_events()
            await ctx.send("Vérification des événements Zunivers terminée ! 🎉", ephemeral=True)
        except Exception as e:
            await send_error(ctx, f"Erreur lors de la vérification: {e}")
            logger.error(f"Manual event check failed: {e}")

    async def _check_zunivers_events(self) -> None:
        """Check current events and detect changes."""
        channel = await self._get_zunivers_channel()
        if not channel:
            return

        for rule_set in [ReminderType.NORMAL, ReminderType.HARDCORE]:
            await self._check_events_for_rule_set(channel, rule_set)

        await self._check_hardcore_season(channel)
        await self.storage.save_event_state(self.event_state)

    async def _check_events_for_rule_set(self, channel: GuildText, rule_set: ReminderType) -> None:
        """Check events for a specific rule set."""
        try:
            current_events = await self.api_client.get_current_events(rule_set)
            previous_state = self.event_state.events.get(rule_set.value, {})
            current_state = {}

            for event_data in current_events:
                event = ZuniversEvent.from_api_response(event_data)
                current_state[event.id] = event.to_state_dict()

                if event.id not in previous_state:
                    if event.is_active:
                        await self._send_event_notification(channel, event_data, "start", rule_set)
                        logger.info(f"New {rule_set.value} event: {event.name}")
                else:
                    prev_active = previous_state[event.id]["is_active"]
                    if prev_active != event.is_active:
                        event_type = "start" if event.is_active else "end"
                        await self._send_event_notification(
                            channel, event_data, event_type, rule_set
                        )
                        logger.info(f"{rule_set.value} event {event_type}: {event.name}")

            for event_id, prev_event in previous_state.items():
                if event_id not in current_state and prev_event["is_active"]:
                    fake_event = {
                        "id": event_id,
                        "name": prev_event["name"],
                        "isActive": False,
                        "beginDate": prev_event["begin_date"],
                        "endDate": prev_event["end_date"],
                    }
                    await self._send_event_notification(channel, fake_event, "end", rule_set)
                    logger.info(f"{rule_set.value} event ended (disappeared): {prev_event['name']}")

            self.event_state.events[rule_set.value] = current_state

        except Exception as e:
            logger.error(f"Error checking {rule_set.value} events: {e}")

    async def _send_event_notification(
        self,
        channel: GuildText,
        event: dict,
        event_type: str,
        rule_set: ReminderType,
    ) -> None:
        """Send an event notification embed to the channel."""
        embed = create_event_embed(event, event_type, rule_set)

        image_file = None
        image_url = event.get("imageUrl")
        if image_url and image_url_needs_download(image_url):
            image_file = await self.api_client.download_image(image_url, "event_image.webp")

        set_event_embed_image(embed, image_url, image_file)

        if image_file:
            await channel.send(embed=embed, file=image_file)
        else:
            await channel.send(embed=embed)

    async def _check_hardcore_season(self, channel: GuildText) -> None:
        """Check for hardcore season changes."""
        try:
            current_data = await self.api_client.get_current_hardcore_season()
            current_season = (
                HardcoreSeason.from_api_response(current_data) if current_data else None
            )
            previous_season = self.event_state.hardcore_season

            if previous_season is None and current_season is not None and current_data:
                embed = create_season_embed(current_data, "start")
                await channel.send(embed=embed)
                logger.info(f"New hardcore season: Season {current_season.index}")

            elif previous_season is not None and current_season is None:
                embed = create_season_embed(previous_season.to_dict(), "end")
                await channel.send(embed=embed)
                logger.info(f"Hardcore season ended: Season {previous_season.index}")

            elif (
                previous_season is not None
                and current_season is not None
                and current_data is not None
                and previous_season.id != current_season.id
            ):
                embed_end = create_season_embed(previous_season.to_dict(), "end")
                embed_start = create_season_embed(current_data, "start")
                await channel.send(embeds=[embed_end, embed_start])
                logger.info(
                    f"Season change: Season {previous_season.index} → {current_season.index}"
                )

            self.event_state.hardcore_season = current_season

        except Exception as e:
            logger.error(f"Error checking hardcore season: {e}")
