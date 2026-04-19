"""Scheduled update loop + donation milestone announcements."""

import asyncio
import os
from datetime import datetime

from interactions import Embed, File, IntervalTrigger, Task, utils

from src.core import logging as logutil
from src.core.http import fetch

from ._common import API_URL, MILESTONE_INTERVAL, STREAMLABS_API_URL, UPDATE_INTERVAL

logger = logutil.init_logger(os.path.basename(__file__))


class TasksMixin:
    """Periodic Zevent refresh that rebuilds and edits the pinned message."""

    @Task.create(IntervalTrigger(seconds=UPDATE_INTERVAL))
    async def zevent(self):
        total_amount = "Données indisponibles"
        total_int = 0

        try:
            logger.debug("Fetching data from APIs...")
            now_date = datetime.now().date()
            target_day = self._get_planning_day(now_date)

            data, streamlabs_data = await asyncio.gather(
                fetch(API_URL, return_type="json"),
                fetch(STREAMLABS_API_URL, return_type="json"),
                return_exceptions=True,
            )

            planning_data = await self._ensure_planning_cache(target_day)

            data = (
                data
                if not isinstance(data, Exception) and self._validate_api_data(data, "zevent")
                else None
            )
            streamlabs_data = (
                streamlabs_data
                if not isinstance(streamlabs_data, Exception)
                and self._validate_api_data(streamlabs_data, "streamlabs")
                else None
            )

            if isinstance(data, Exception):
                logger.error(f"Failed to fetch Zevent API: {data}")
            if isinstance(streamlabs_data, Exception):
                logger.error(f"Failed to fetch Streamlabs API: {streamlabs_data}")
            if planning_data is None:
                logger.warning("Planning data not available")

            concert_active = await self._is_concert_active()

            if not self._is_event_started():
                if data:
                    streams = await self.categorize_streams(self._safe_get_data(data, ["live"], []))
                    embeds = [
                        self.create_main_embed("0 €"),
                        self.create_location_embed(
                            "streamers présents sur place",
                            streams["LAN"],
                            withlink=False,
                            viewers_count=None,
                            total_count=self._get_stream_total_count(streams, "LAN"),
                        ),
                        self.create_location_embed(
                            "participants à distance",
                            streams["Online"],
                            withlink=False,
                            viewers_count=None,
                            total_count=self._get_stream_total_count(streams, "Online"),
                        ),
                    ]

                    top_donations_embed = self.create_top_donations_embed(
                        self._safe_get_data(data, ["live"], [])
                    )
                    if top_donations_embed:
                        embeds.append(top_donations_embed)

                    if planning_data and isinstance(planning_data, list):
                        embeds.append(await self.create_planning_embed(planning_data))
                else:
                    embeds = [self.create_main_embed("0 €")]

                embeds = self.ensure_embeds_fit_limit(embeds)

                file = File("data/Zevent_logo.png")
                if self.message:
                    await self.message.edit(embeds=embeds, content="", files=[file])
                    logger.debug("Pre-event countdown message updated successfully")
                return
            if concert_active or not self._is_main_event_started():
                if data:
                    total_amount, total_int = self.get_total_amount(data, streamlabs_data)
                    streams = await self.categorize_streams(self._safe_get_data(data, ["live"], []))

                    main_embed_status = "concert_live" if concert_active else "concert_window"
                    embeds = [
                        self.create_main_embed(total_amount, concert_status=main_embed_status),
                        self.create_location_embed(
                            "streamers présents sur place",
                            streams["LAN"],
                            withlink=False,
                            viewers_count=None,
                            total_count=self._get_stream_total_count(streams, "LAN"),
                        ),
                        self.create_location_embed(
                            "participants à distance",
                            streams["Online"],
                            withlink=False,
                            viewers_count=None,
                            total_count=self._get_stream_total_count(streams, "Online"),
                        ),
                    ]

                    top_donations_embed = self.create_top_donations_embed(
                        self._safe_get_data(data, ["live"], [])
                    )
                    if top_donations_embed:
                        embeds.append(top_donations_embed)

                    if planning_data and isinstance(planning_data, list):
                        embeds.append(await self.create_planning_embed(planning_data))
                else:
                    embeds = [self.create_main_embed("Données indisponibles")]

                embeds = self.ensure_embeds_fit_limit(embeds)

                file = File("data/Zevent_logo.png")
                if self.message:
                    await self.message.edit(embeds=embeds, content="", files=[file])
                    logger.debug("Concert phase message updated successfully")

                await self.check_and_send_milestone(total_int if data else 0)
                return

            if not data and not self.last_data_cache:
                logger.error("All APIs failed and no cached data available")
                await self.send_simplified_update(total_amount)
                return

            if not data and self.last_data_cache:
                logger.warning("Using cached data due to API failure")
                data = self.last_data_cache
            elif isinstance(data, dict):
                self.last_data_cache = data
                self.last_update_time = datetime.now()

            if isinstance(data, dict):
                if not isinstance(streamlabs_data, dict):
                    streamlabs_data = None

                total_amount, total_int = self.get_total_amount(data, streamlabs_data)
                streams = await self.categorize_streams(self._safe_get_data(data, ["live"], []))
                viewers_data = await self.get_viewers_by_location(
                    self._safe_get_data(data, ["live"], [])
                )

                embeds = [
                    self.create_main_embed(total_amount, viewers_data["Total"]),
                    self.create_location_embed(
                        "streamers présents sur place",
                        streams["LAN"],
                        withlink=False,
                        viewers_count=viewers_data["LAN"],
                        total_count=self._get_stream_total_count(streams, "LAN"),
                    ),
                    self.create_location_embed(
                        "participants à distance",
                        streams["Online"],
                        withlink=False,
                        viewers_count=viewers_data["Online"],
                        total_count=self._get_stream_total_count(streams, "Online"),
                    ),
                ]

                top_donations_embed = self.create_top_donations_embed(
                    self._safe_get_data(data, ["live"], [])
                )
                if top_donations_embed:
                    embeds.append(top_donations_embed)

                if planning_data and isinstance(planning_data, list):
                    embeds.append(await self.create_planning_embed(planning_data))

                embeds = self.ensure_embeds_fit_limit(embeds)

                file = File("data/Zevent_logo.png")

                if self.message:
                    await self.message.edit(embeds=embeds, content="", files=[file])
                    logger.debug("Message updated successfully")

                await self.check_and_send_milestone(total_int)

        except Exception as e:
            logger.error(f"Unexpected error in zevent task: {e}")
            await self.send_simplified_update(total_amount)

    async def check_and_send_milestone(self, total_amount: float):
        current_milestone = int(total_amount // MILESTONE_INTERVAL * MILESTONE_INTERVAL)

        if current_milestone > self.last_milestone:
            if self.last_milestone != 0:
                milestone_message = (
                    f"🎉 Nouveau palier atteint : {current_milestone:,} € récoltés ! 🎉".replace(
                        ",", " "
                    )
                )
                if self.channel and hasattr(self.channel, "send"):
                    await self.channel.send(milestone_message)
                else:
                    logger.error(
                        "Cannot send milestone message: channel not available or doesn't support sending"
                    )
            self.last_milestone = current_milestone

    async def send_simplified_update(self, total_amount: str):
        """Fallback embed used when API fetch and cache both fail."""
        try:
            simple_embed = Embed(
                title="Zevent Update",
                description=f"Total récolté: {total_amount}\n\nDésolé, nous rencontrons des difficultés techniques pour afficher les détails des streamers.",
                color=0x59AF37,
            )
            simple_embed.timestamp = utils.timestamp_converter(datetime.now())

            if self.message:
                await self.message.edit(embeds=[simple_embed], content="")
        except Exception as e:
            logger.error(f"Failed to send simplified update: {e}")
