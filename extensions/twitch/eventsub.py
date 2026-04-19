"""Twitch EventSub websocket lifecycle and live-state event handlers."""

import asyncio
import os
import signal

from interactions import IntervalTrigger, OrTrigger, TimeTrigger, listen
from twitchAPI.eventsub.websocket import EventSubWebsocket
from twitchAPI.helper import first
from twitchAPI.oauth import UserAuthenticationStorageHelper
from twitchAPI.object.eventsub import (
    ChannelUpdateEvent,
    StreamOfflineEvent,
    StreamOnlineEvent,
)
from twitchAPI.twitch import Twitch
from twitchAPI.type import AuthScope

from src.core import logging as logutil

from ._common import ensure_utc

logger = logutil.init_logger(os.path.basename(__file__))


class EventSubMixin:
    """Websocket subscription management and EventSub callback handlers."""

    async def get_stream_data(self, user_id: str):
        """Return the live Stream object for a user (or None if offline/error)."""
        try:
            return await first(self.twitch.get_streams(user_id=user_id))
        except Exception as e:
            logger.error(f"Error getting stream data for user {user_id}: {e}")
            return None

    async def run(self):
        """Start the Twitch API + EventSub websocket and subscribe all streamers."""
        try:
            self.twitch = await Twitch(self.client_id, self.client_secret)
            helper = UserAuthenticationStorageHelper(
                twitch=self.twitch,
                scopes=[AuthScope.USER_READ_SUBSCRIPTIONS],
                storage_path="./data/twitchcreds.json",
            )
            await helper.bind()

            self.eventsub = EventSubWebsocket(
                self.twitch,
                callback_loop=asyncio.get_event_loop(),
                revocation_handler=self.on_revocation,
            )
            logger.info("Starting EventSub")
            self.eventsub.start()

            for _, streamer in self.streamers.items():
                try:
                    user = await first(self.twitch.get_users(logins=[streamer.streamer_id]))
                    if user:
                        streamer.user_id = user.id

                        await self.eventsub.listen_stream_online(
                            broadcaster_user_id=user.id, callback=self.on_live_start
                        )
                        await self.eventsub.listen_stream_offline(
                            broadcaster_user_id=user.id, callback=self.on_live_end
                        )
                        await self.eventsub.listen_channel_update_v2(
                            broadcaster_user_id=user.id, callback=self.on_update
                        )
                        logger.info(
                            f"Registered event subscriptions for {streamer.streamer_id} (ID: {user.id})"
                        )
                    else:
                        logger.error(f"Could not find Twitch user for {streamer.streamer_id}")
                except Exception as e:
                    logger.error(f"Error subscribing to events for {streamer.streamer_id}: {e}")

            await self.update()

            signal.signal(signal.SIGTERM, self.stop_on_signal)
            while self.stop is False:
                await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Error in run method: {e}")
            await asyncio.sleep(30)
            asyncio.create_task(self.run())

    @listen()
    async def on_revocation(self, data):
        logger.error("Revocation detected: %s", data)
        await self.eventsub.stop()
        await self.twitch.close()
        asyncio.create_task(self.run())

    async def on_live_start(self, data: StreamOnlineEvent):
        """EventSub callback: a streamer just went live."""
        user_id = data.event.broadcaster_user_id
        broadcaster_name = data.event.broadcaster_user_name
        logger.info("Stream is live: %s", broadcaster_name)

        streamers = self.get_streamer_by_user_id(user_id)
        for streamer in streamers:
            try:
                stream = await self.get_stream_data(user_id)
                if stream:
                    streamer.stream_start_time = ensure_utc(stream.started_at)
                    streamer.stream_title = stream.title
                    streamer.stream_id = stream.id
                    streamer.stream_categories = [stream.game_name] if stream.game_name else []
                    logger.info(
                        f"Stored stream info for {streamer.streamer_id}: started at {streamer.stream_start_time}"
                    )

                await self.send_stream_start_notification(streamer, broadcaster_name, stream)
                await self.edit_message(streamer, offline=False)
            except Exception as e:
                logger.error(
                    f"Error handling live start for {streamer.streamer_id} in guild {streamer.guild_id}: {e}"
                )

        for streamer_id in {s.streamer_id for s in streamers}:
            await self._save_streamer_state(streamer_id)

        self.update.reschedule(IntervalTrigger(minutes=15))

    async def on_live_end(self, data: StreamOfflineEvent):
        """EventSub callback: a streamer just went offline."""
        user_id = data.event.broadcaster_user_id
        broadcaster_name = data.event.broadcaster_user_name
        logger.info("Stream is offline: %s", broadcaster_name)

        streamers = self.get_streamer_by_user_id(user_id)
        for streamer in streamers:
            try:
                await self.send_stream_end_notification(streamer, broadcaster_name)
                await self.edit_message(streamer, offline=True)

                streamer.stream_start_time = None
                streamer.stream_title = None
                streamer.stream_id = None
                streamer.stream_categories = []
                # Keep last_notified_title / last_notified_category so off-live
                # updates can still be diffed.
            except Exception as e:
                logger.error(
                    f"Error handling live end for {streamer.streamer_id} in guild {streamer.guild_id}: {e}"
                )

        for streamer_id in {s.streamer_id for s in streamers}:
            await self._save_streamer_state(streamer_id)

        self.update.reschedule(
            OrTrigger(
                TimeTrigger(hour=2, utc=False),
                TimeTrigger(hour=6, utc=False),
                TimeTrigger(hour=10, utc=False),
                TimeTrigger(hour=14, utc=False),
                TimeTrigger(hour=18, utc=False),
                TimeTrigger(hour=22, utc=False),
            )
        )

    async def on_update(self, data: ChannelUpdateEvent):
        """EventSub callback: the channel title/category changed."""
        user_id = data.event.broadcaster_user_id
        user_name = data.event.broadcaster_user_name
        logger.info(
            "Channel updated: %s (ID : %s)\nCategory: %s(ID : %s)\nTitle: %s\nContent classification: %s\nLanguage: %s\n",
            user_name,
            user_id,
            data.event.category_name,
            data.event.category_id,
            data.event.title,
            ", ".join(data.event.content_classification_labels),
            data.event.language,
        )

        streamers = self.get_streamer_by_user_id(user_id)
        raw_category = (data.event.category_name or "").strip()
        for streamer in streamers:
            try:
                stream = await self.get_stream_data(user_id)

                if (
                    stream is not None
                    and raw_category
                    and (
                        not streamer.stream_categories
                        or streamer.stream_categories[-1] != raw_category
                    )
                ):
                    streamer.stream_categories.append(raw_category)

                new_title = self.get_display_value(data.event.title, "Titre non renseigné")
                new_category = self.get_display_value(
                    data.event.category_name, "Catégorie non renseignée"
                )

                title_changed = streamer.last_notified_title != new_title
                category_changed = streamer.last_notified_category != new_category

                if (
                    streamer.notif_channel
                    and streamer.notify_stream_update
                    and (title_changed or category_changed)
                ):
                    streamer.last_notified_title = new_title
                    streamer.last_notified_category = new_category

                    update_embed = await self.create_notification_embed(
                        streamer.guild_id,
                        title="Mise à jour du live Twitch",
                        description=f"{user_name} a modifié les informations du live.",
                    )
                    update_embed.add_field(name="Titre", value=new_title, inline=False)
                    update_embed.add_field(name="Catégorie", value=new_category, inline=False)
                    if not stream:
                        update_embed.add_field(
                            name="Statut",
                            value="Modification détectée hors live",
                            inline=False,
                        )

                    await streamer.notif_channel.send(embed=update_embed)
                elif not (title_changed or category_changed):
                    logger.debug(
                        f"Skipping duplicate notification for {streamer.streamer_id} - title and category unchanged"
                    )

                await self.edit_message(streamer, offline=(stream is None), data=data)
            except Exception as e:
                logger.error(
                    f"Error handling update for {streamer.streamer_id} in guild {streamer.guild_id}: {e}"
                )

        for streamer_id in {s.streamer_id for s in streamers}:
            await self._save_streamer_state(streamer_id)

        stream_checks = []
        for streamer in self.streamers.values():
            if streamer.user_id:
                stream_data = await self.get_stream_data(streamer.user_id)
                stream_checks.append(stream_data is not None)

        if any(stream_checks):
            self.update.reschedule(IntervalTrigger(minutes=15))
