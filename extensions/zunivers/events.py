"""EventsMixin — Zunivers event tracking and hardcore season notifications."""

import io

from interactions import (
    File,
    GuildText,
    OrTrigger,
    SlashContext,
    Task,
    TimeTrigger,
)

from features.coloc.constants import ReminderType
from features.coloc.models import HardcoreSeason, ZuniversEvent
from features.coloc.utils import image_url_needs_download
from src.discord_ext.messages import send_error

from ._common import GuildState, logger, zunivers_api_url, zunivers_cmd
from .embeds import create_event_embed, create_season_embed, set_event_embed_image


class EventsMixin:
    """Hourly Zunivers event polling + hardcore season state transitions."""

    @Task.create(OrTrigger(*[TimeTrigger(hour=i, minute=1) for i in range(24)]))
    async def events_checker(self):
        """Check for Zunivers event changes every hour."""
        await self._check_zunivers_events()

    @zunivers_cmd.subcommand(
        sub_cmd_name="check",
        sub_cmd_description="Vérifie manuellement les événements Zunivers",
    )
    async def zunivers_check(self, ctx: SlashContext):
        """Manually trigger the event check for this guild."""
        if not zunivers_api_url():
            await send_error(ctx, "L'API ZUnivers n'est pas configurée (section globale ZUnivers).")
            return
        await ctx.defer()
        try:
            await self._check_zunivers_events(only=str(ctx.guild_id))
            await ctx.send("Vérification des événements Zunivers terminée ! 🎉", ephemeral=True)
        except Exception as e:
            await send_error(ctx, f"Erreur lors de la vérification: {e}")
            logger.error(f"Manual event check failed: {e}")

    async def _check_zunivers_events(self, only: str | None = None) -> None:
        """Compare the current events with each enabled guild's last known state.

        The events and the hardcore season are the same for every guild: they
        are fetched once, then each guild is told what changed since its own
        last check.
        """
        guilds = [(state, settings) for state, settings in await self._guilds()]
        if only is not None:
            guilds = [(state, settings) for state, settings in guilds if state.guild_id == only]
        if not guilds:
            return
        if not zunivers_api_url():
            logger.warning("No ZUnivers API URL (zunivers.apiUrl): skipping the events check")
            return

        current_events: dict[ReminderType, list[dict]] = {}
        for rule_set in [ReminderType.NORMAL, ReminderType.HARDCORE]:
            try:
                current_events[rule_set] = await self.api_client.get_current_events(rule_set)
            except Exception as e:
                logger.error(f"Error checking {rule_set.value} events: {e}")
        try:
            season_data = await self.api_client.get_current_hardcore_season()
            season_known = True
        except Exception as e:
            logger.error(f"Error checking hardcore season: {e}")
            season_data, season_known = None, False

        for state, settings in guilds:
            channel = await self._zunivers_channel(settings)
            if not channel:
                continue
            for rule_set, events in current_events.items():
                await self._check_events_for_rule_set(state, channel, rule_set, events)
            if season_known:
                await self._check_hardcore_season(state, channel, settings, season_data)
            await state.storage.save_event_state(state.event_state)

    async def _check_events_for_rule_set(
        self,
        state: GuildState,
        channel: GuildText,
        rule_set: ReminderType,
        current_events: list[dict],
    ) -> None:
        """Announce the events of *rule_set* that started or ended for this guild."""
        try:
            previous_state = state.event_state.events.get(rule_set.value, {})
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

            state.event_state.events[rule_set.value] = current_state

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
            image_bytes = await self.api_client.download_image(image_url)
            if image_bytes:
                image_file = File(file=io.BytesIO(image_bytes), file_name="event_image.webp")

        set_event_embed_image(embed, image_url, image_file)

        if image_file:
            await channel.send(embed=embed, file=image_file)
        else:
            await channel.send(embed=embed)

    async def _check_hardcore_season(
        self,
        state: GuildState,
        channel: GuildText,
        settings: dict,
        current_data: dict | None,
    ) -> None:
        """Announce a hardcore season that started, ended or changed for this guild."""
        try:
            current_season = (
                HardcoreSeason.from_api_response(current_data) if current_data else None
            )
            previous_season = state.event_state.hardcore_season

            if previous_season is None and current_season is not None and current_data:
                embed = create_season_embed(current_data, "start", settings)
                await channel.send(embed=embed)
                logger.info(f"New hardcore season: Season {current_season.index}")

            elif previous_season is not None and current_season is None:
                embed = create_season_embed(previous_season.to_dict(), "end", settings)
                await channel.send(embed=embed)
                logger.info(f"Hardcore season ended: Season {previous_season.index}")

            elif (
                previous_season is not None
                and current_season is not None
                and current_data is not None
                and previous_season.id != current_season.id
            ):
                embed_end = create_season_embed(previous_season.to_dict(), "end", settings)
                embed_start = create_season_embed(current_data, "start", settings)
                await channel.send(embeds=[embed_end, embed_start])
                logger.info(
                    f"Season change: Season {previous_season.index} → {current_season.index}"
                )

            state.event_state.hardcore_season = current_season

        except Exception as e:
            logger.error(f"Error checking hardcore season: {e}")
