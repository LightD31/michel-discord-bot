"""Twitch EventSub websocket lifecycle and live-state event handlers."""

import asyncio
import contextlib
import os
import signal

from interactions import IntervalTrigger, OrTrigger, Task, TimeTrigger
from twitchAPI.eventsub.websocket import EventSubWebsocket
from twitchAPI.helper import first
from twitchAPI.oauth import UserAuthenticationStorageHelper
from twitchAPI.object.eventsub import (
    ChannelUpdateEvent,
    StreamOfflineEvent,
    StreamOnlineEvent,
)
from twitchAPI.twitch import Twitch
from twitchAPI.type import AuthScope, EventSubSubscriptionConflict

from src.core import logging as logutil
from src.core.tasks import spawn

from ._common import ensure_utc

logger = logutil.init_logger(os.path.basename(__file__))

# EventSub topics subscribed for every tracked broadcaster, as
# (EventSubWebsocket method, handler attribute) pairs.
EVENT_SUBSCRIPTIONS = (
    ("listen_stream_online", "on_live_start"),
    ("listen_stream_offline", "on_live_end"),
    ("listen_channel_update_v2", "on_update"),
)


class EventSubMixin:
    """Websocket subscription management and EventSub callback handlers."""

    async def get_stream_data(self, user_id: str):
        """Return the live Stream object for a user (or None if offline/error)."""
        try:
            return await first(self.twitch.get_streams(user_id=user_id))
        except Exception as e:
            logger.error(f"Error getting stream data for user {user_id}: {e}")
            return None

    def eventsub_is_alive(self) -> bool:
        """Whether the EventSub websocket can still deliver events.

        twitchAPI gives us no public liveness flag, and its own state is not
        enough: when the receive loop hits a websocket error (or fails to
        re-establish a dropped connection) it breaks out for good, yet
        ``_running`` stays ``True`` and ``active_session`` keeps pointing at the
        dead session. The socket thread even stays alive, so the process looks
        perfectly healthy while every EventSub notification silently stops.
        The receive task finishing is the only reliable signal, so check it.
        """
        if self.eventsub is None or self.twitch is None:
            return False
        if not getattr(self.eventsub, "_running", False):
            return False
        if self.eventsub.active_session is None:
            return False
        tasks = getattr(self.eventsub, "_tasks", None) or []
        return not any(task.done() for task in tasks)

    async def _shutdown_eventsub(self) -> None:
        """Best-effort teardown of the current websocket and Twitch client."""
        if self.eventsub is not None:
            try:
                await self.eventsub.stop()
            except Exception as e:
                logger.debug("EventSub was not running: %s", e)
            self.eventsub = None
        if self.twitch is not None:
            try:
                await self.twitch.close()
            except Exception as e:
                logger.debug("Error closing Twitch client: %s", e)
            self.twitch = None

    async def _subscribe_streamer(self, user_id: str, streamer_id: str) -> None:
        """Subscribe every EventSub topic for one broadcaster.

        Each topic is subscribed independently on purpose: sharing a single
        ``try`` meant that one failure (a duplicate subscription, a transient
        Twitch error) skipped the topics registered after it. Because
        ``stream.online`` came first, that left channels receiving live
        notifications while stream updates and stream ends never arrived.
        """
        for method_name, handler_name in EVENT_SUBSCRIPTIONS:
            try:
                await getattr(self.eventsub, method_name)(
                    broadcaster_user_id=user_id,
                    callback=getattr(self, handler_name),
                )
            except EventSubSubscriptionConflict:
                logger.debug("%s already subscribed for %s", method_name, streamer_id)
            except Exception as e:
                logger.error("Error subscribing %s for %s: %s", method_name, streamer_id, e)

    async def _start_eventsub(self) -> None:
        """(Re)create the Twitch client + websocket and subscribe every streamer."""
        await self._shutdown_eventsub()

        self.twitch = await Twitch(self.client_id, self.client_secret)
        helper = UserAuthenticationStorageHelper(
            twitch=self.twitch,
            scopes=[AuthScope.USER_READ_SUBSCRIPTIONS],
            storage_path="./data/twitchcreds.json",
        )
        await helper.bind()

        self.eventsub = EventSubWebsocket(
            self.twitch,
            callback_loop=asyncio.get_running_loop(),
            revocation_handler=self.on_revocation,
        )
        logger.info("Starting EventSub")
        self.eventsub.start()

        # Resolve every configured login, but subscribe once per broadcaster:
        # the same streamer can be followed by several guilds and Twitch
        # rejects duplicate subscriptions on a session with a 409.
        subscribed: set[str] = set()
        for streamer in self.streamers.values():
            try:
                user = await first(self.twitch.get_users(logins=[streamer.streamer_id]))
            except Exception as e:
                logger.error("Error resolving Twitch user %s: %s", streamer.streamer_id, e)
                continue
            if not user:
                logger.error("Could not find Twitch user for %s", streamer.streamer_id)
                continue

            streamer.user_id = user.id
            if user.id in subscribed:
                continue
            subscribed.add(user.id)
            await self._subscribe_streamer(user.id, streamer.streamer_id)
            logger.info(
                "Registered event subscriptions for %s (ID: %s)", streamer.streamer_id, user.id
            )

        await self.update()

        # Only valid from the main thread; the watchdog in main.py covers the rest.
        with contextlib.suppress(ValueError):
            signal.signal(signal.SIGTERM, self.stop_on_signal)

    async def run(self):
        """Start (or restart) the Twitch API + EventSub websocket.

        Safe to call repeatedly: concurrent calls are collapsed and any previous
        websocket is torn down first, so reconnects can't pile up orphaned
        sockets. Returns once EventSub is up rather than parking a coroutine on
        a sleep loop.
        """
        if self._run_lock.locked():
            logger.info("EventSub startup already in progress, skipping duplicate run()")
            return

        async with self._run_lock:
            while not self.stop:
                try:
                    await self._start_eventsub()
                except Exception as e:
                    logger.error("Error starting EventSub: %s", e)
                else:
                    return
                await asyncio.sleep(30)

    @Task.create(IntervalTrigger(minutes=5))
    async def eventsub_watchdog(self):
        """Restart EventSub when its websocket has silently gone deaf.

        Without this, a dropped connection ends notifications until someone
        restarts the bot — the scheduled ``update`` task keeps refreshing the
        planning message and Discord event by polling, so nothing else looks
        broken.
        """
        if self.stop or self._run_lock.locked() or self.eventsub_is_alive():
            return
        logger.warning("EventSub websocket is no longer receiving events, restarting it")
        await self.run()

    async def on_revocation(self, data):
        """twitchAPI callback fired when Twitch revokes one of our subscriptions.

        This is not a Discord event: decorating it with ``@listen()`` replaced it
        with an unbound ``Listener``, so twitchAPI's revocation path raised a
        ``TypeError`` and EventSub was never restarted.
        """
        logger.error("Revocation detected: %s", data)
        spawn(self.run(), name="twitch-eventsub-revocation-restart", log=logger)

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
