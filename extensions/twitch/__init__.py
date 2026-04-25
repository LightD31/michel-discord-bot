"""Twitch extension package.

The Extension class itself stays thin: __init__, lifecycle listeners, the
planning-setup slash command, and per-streamer state helpers. All embed
construction, EventSub websocket handling, scheduled-event sync, and emote
change detection live in their own submodules (mixin classes combined below).
"""

import asyncio
import json
import os
from collections import defaultdict

import pytz
from interactions import (
    AutocompleteContext,
    BaseChannel,
    ChannelType,
    Client,
    Extension,
    OptionType,
    Permissions,
    SlashContext,
    listen,
    slash_command,
    slash_default_member_permission,
    slash_option,
)

from src.core import logging as logutil
from src.core.config import CONFIG_PATH, load_config
from src.core.db import mongo_manager
from src.discord_ext.messages import send_error, send_success

from ._common import StreamerInfo, ensure_utc
from .emotes import EMOTE_CACHE_DIR, EmotesMixin
from .eventsub import EventSubMixin
from .notifications import NotificationsMixin
from .schedule import ScheduleMixin

logger = logutil.init_logger(os.path.basename(__file__))


def _save_streamer_channel_message(
    guild_id: str,
    streamer_id: str,
    channel_id: str,
    message_id: str,
    pin: bool,
) -> None:
    """Persist planning channel/message for a specific streamer in config.json."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as file:
            data = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return
    guild = data.setdefault("servers", {}).setdefault(str(guild_id), {})
    mod = guild.setdefault("moduleTwitch", {})
    streamer_list = mod.setdefault("twitchStreamerList", {})
    streamer_cfg = streamer_list.setdefault(streamer_id, {})
    streamer_cfg["twitchPlanningChannelId"] = str(channel_id)
    streamer_cfg["twitchPlanningMessageId"] = str(message_id)
    streamer_cfg["twitchPlanningPinMessage"] = pin
    with open(CONFIG_PATH, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)


class TwitchExtension(
    Extension,
    NotificationsMixin,
    EventSubMixin,
    ScheduleMixin,
    EmotesMixin,
):
    """Aggregate the four feature mixins into a single Discord extension."""

    def __init__(self, bot: Client):
        self.bot: Client = bot
        self.config, self.module_config, self.enabled_servers = load_config("moduleTwitch")
        self.client_id = self.config["twitch"]["twitchClientId"]
        self.client_secret = self.config["twitch"]["twitchClientSecret"]

        self.streamers: dict[str, StreamerInfo] = {}
        self.init_streamers()

        self.eventsub = None
        self.twitch = None
        self.stop = False
        self.timezone = pytz.timezone("Europe/Paris")

        os.makedirs(EMOTE_CACHE_DIR, exist_ok=True)

    # ─── Streamer registry ────────────────────────────────────────────

    def init_streamers(self) -> None:
        """Build the per-(guild, streamer) StreamerInfo registry from config."""
        for guild_id in self.enabled_servers:
            server_config = self.module_config[guild_id]
            streamer_list = server_config.get("twitchStreamerList", {})

            for streamer_id, streamer_config in streamer_list.items():
                streamer_key = f"{guild_id}_{streamer_id}"
                self.streamers[streamer_key] = StreamerInfo(
                    guild_id=int(guild_id),
                    streamer_id=streamer_id,
                    config=streamer_config,
                )

    def get_streamer_by_user_id(self, user_id: str) -> list[StreamerInfo]:
        """All StreamerInfo entries that map to the given Twitch user ID."""
        return [s for s in self.streamers.values() if s.user_id == user_id]

    def get_streamer_key(self, guild_id: int, streamer_id: str) -> str:
        return f"{guild_id}_{streamer_id}"

    # ─── Scheduled-event matching ─────────────────────────────────────

    @staticmethod
    def _event_location(event) -> str:
        """Best-effort extraction of the external_location URL from a scheduled event."""
        meta = getattr(event, "entity_metadata", None)
        if meta is not None:
            location = getattr(meta, "location", None)
            if location:
                return str(location)
        # Fallback for older interactions.py versions that surface it directly.
        return str(getattr(event, "location", "") or "")

    def _match_scheduled_event(self, streamer: StreamerInfo, bot_events: list):
        """Pick the scheduled event for ``streamer`` from the guild's bot-owned events.

        Each streamer's event has ``external_location =
        https://twitch.tv/{user_login}``; the configured ``streamer_id`` key is
        the same login, so we match on that URL.

        Mutates ``bot_events`` to remove the claimed event so the next streamer
        in the same guild can't be assigned the same event.
        """
        expected = f"https://twitch.tv/{streamer.streamer_id}".lower()
        for i, event in enumerate(bot_events):
            if self._event_location(event).lower() == expected:
                return bot_events.pop(i)

        # Legacy fallback: events created before URL disambiguation existed
        # had no reliable per-streamer marker. Claim one only if this guild
        # tracks a single streamer; otherwise leave the event alone — the next
        # update tick will recreate it for whichever streamer needs it.
        guild_streamer_count = sum(
            1 for s in self.streamers.values() if s.guild_id == streamer.guild_id
        )
        if guild_streamer_count == 1 and bot_events:
            return bot_events.pop(0)
        return None

    # ─── Session-state persistence ────────────────────────────────────

    @staticmethod
    def _streamer_state_collection():
        return mongo_manager.get_global_collection("twitch_streamer_state")

    async def _load_streamer_states(self) -> None:
        """Hydrate StreamerInfo session fields from MongoDB at startup."""
        try:
            states: dict[str, dict] = {}
            async for doc in self._streamer_state_collection().find():
                states[doc["_id"]] = doc
        except Exception as e:
            logger.error("Failed to load Twitch streamer states: %s", e)
            return

        for streamer in self.streamers.values():
            doc = states.get(streamer.streamer_id)
            if not doc:
                continue
            start = doc.get("stream_start_time")
            streamer.stream_start_time = ensure_utc(start) if start else None
            streamer.stream_title = doc.get("stream_title")
            streamer.stream_id = doc.get("stream_id")
            streamer.last_notified_title = doc.get("last_notified_title")
            streamer.last_notified_category = doc.get("last_notified_category")
            stored_categories = doc.get("stream_categories")
            streamer.stream_categories = (
                list(stored_categories) if isinstance(stored_categories, list) else []
            )

    async def _save_streamer_state(self, streamer_id: str) -> None:
        """Persist the session fields of a streamer (shared across guilds)."""
        ref = next(
            (s for s in self.streamers.values() if s.streamer_id == streamer_id),
            None,
        )
        if ref is None:
            return
        doc = {
            "stream_start_time": ref.stream_start_time,
            "stream_title": ref.stream_title,
            "stream_id": ref.stream_id,
            "last_notified_title": ref.last_notified_title,
            "last_notified_category": ref.last_notified_category,
            "stream_categories": list(ref.stream_categories),
        }
        try:
            await self._streamer_state_collection().update_one(
                {"_id": streamer_id}, {"$set": doc}, upsert=True
            )
        except Exception as e:
            logger.error("Failed to save Twitch state for %s: %s", streamer_id, e)

    # ─── Lifecycle ────────────────────────────────────────────────────

    @listen()
    async def on_startup(self):
        """Restore state, hydrate channels/messages, and start background tasks."""
        logger.info("Waiting for bot to be ready")
        await self.bot.wait_until_ready()

        await self._load_streamer_states()

        # Pre-fetch each guild's bot-owned scheduled events once so we can
        # disambiguate events by their external_location URL when multiple
        # streamers share a guild.
        streamers_by_guild: dict[int, list[StreamerInfo]] = defaultdict(list)
        for streamer in self.streamers.values():
            streamers_by_guild[streamer.guild_id].append(streamer)

        bot_events_by_guild: dict[int, list] = {}
        for guild_id in streamers_by_guild:
            try:
                guild = await self.bot.fetch_guild(guild_id)
                events = await guild.list_scheduled_events()
            except Exception as e:
                logger.error("Error fetching scheduled events for guild %s: %s", guild_id, e)
                bot_events_by_guild[guild_id] = []
                continue
            owned = []
            for event in events:
                try:
                    creator = await event.creator
                except Exception:
                    continue
                if creator and creator.id == self.bot.user.id:
                    owned.append(event)
            bot_events_by_guild[guild_id] = owned

        for streamer_key, streamer in self.streamers.items():
            try:
                if streamer.planning_channel_id:
                    streamer.channel = await self.bot.fetch_channel(streamer.planning_channel_id)

                    msg = None
                    if (
                        streamer.planning_message_id
                        and streamer.channel
                        and hasattr(streamer.channel, "fetch_message")
                    ):
                        try:
                            msg = await streamer.channel.fetch_message(streamer.planning_message_id)
                        except Exception as e:
                            logger.warning(
                                "Streamer %s: could not fetch planning message %s (%s); recreating",
                                streamer.streamer_id,
                                streamer.planning_message_id,
                                e,
                            )
                    if msg is None and streamer.channel and hasattr(streamer.channel, "send"):
                        msg = await streamer.channel.send(
                            f"Initialisation du planning de {streamer.streamer_id}…"
                        )
                        if streamer.planning_pin:
                            try:
                                await msg.pin()
                            except Exception as e:
                                logger.warning("Could not pin planning message: %s", e)
                        _save_streamer_channel_message(
                            str(streamer.guild_id),
                            streamer.streamer_id,
                            str(streamer.channel.id),
                            str(msg.id),
                            streamer.planning_pin,
                        )
                        streamer.planning_message_id = int(msg.id)
                    streamer.message = msg

                if streamer.notification_channel_id:
                    streamer.notif_channel = await self.bot.fetch_channel(
                        streamer.notification_channel_id
                    )

                # Attach the bot-owned scheduled event whose external_location
                # URL matches this streamer's Twitch login (case-insensitive).
                # Falls back to claiming an unclaimed bot event only when this
                # is the sole streamer in the guild — preserves legacy behavior
                # for events created before URL disambiguation existed.
                streamer.scheduled_event = self._match_scheduled_event(
                    streamer, bot_events_by_guild.get(streamer.guild_id, [])
                )
            except Exception as e:
                logger.error(f"Error initializing channels for streamer {streamer_key}: {e}")

        self.check_new_emotes.start()
        logger.info("Starting TwitchExtension")

    @listen()
    async def on_ready(self):
        try:
            await self.eventsub.stop()
        except Exception:
            logger.info("EventSub is not running")
        await self.bot.wait_until_ready()
        asyncio.create_task(self.run())
        self.update.start()

    def stop_on_signal(self, signum, frame):
        """SIGTERM handler — trigger graceful shutdown."""
        self.stop = True
        logger.info("Stopping TwitchExtension")
        asyncio.create_task(self.cleanup())

    async def cleanup(self):
        """Close the EventSub websocket + Twitch client then stop the bot."""
        try:
            await self.eventsub.stop()
            await self.twitch.close()
        except Exception as e:
            logger.error("Error during cleanup: %s", e)
        else:
            logger.info("TwitchExtension stopped")
            await self.bot.stop()

    # ─── Slash commands ───────────────────────────────────────────────

    @slash_command(
        name="twitch-planning-setup",
        description="Créer/attacher le message de planning Twitch d'un streamer",
    )
    @slash_option(
        name="streamer",
        description="ID du streamer configuré",
        opt_type=OptionType.STRING,
        required=True,
        autocomplete=True,
    )
    @slash_option(
        name="channel",
        description="Canal où créer le message de planning",
        opt_type=OptionType.CHANNEL,
        required=True,
        channel_types=[ChannelType.GUILD_TEXT, ChannelType.GUILD_NEWS],
    )
    @slash_option(
        name="pin",
        description="Épingler le message",
        opt_type=OptionType.BOOLEAN,
        required=False,
    )
    @slash_default_member_permission(Permissions.MANAGE_GUILD)
    async def twitch_planning_setup(
        self,
        ctx: SlashContext,
        streamer: str,
        channel: BaseChannel,
        pin: bool = False,
    ):
        if not ctx.guild:
            await send_error(ctx, "Commande utilisable uniquement dans un serveur.")
            return
        streamer_key = self.get_streamer_key(int(ctx.guild.id), streamer)
        streamer_info = self.streamers.get(streamer_key)
        if streamer_info is None:
            await send_error(ctx, f"Streamer '{streamer}' non trouvé pour ce serveur.")
            return
        msg = await channel.send(f"Initialisation du planning de {streamer}…")
        if pin:
            try:
                await msg.pin()
            except Exception as e:
                logger.warning("Could not pin planning message: %s", e)
        _save_streamer_channel_message(
            str(ctx.guild.id), streamer, str(channel.id), str(msg.id), pin
        )
        streamer_info.planning_channel_id = int(channel.id)
        streamer_info.planning_message_id = int(msg.id)
        streamer_info.planning_pin = pin
        streamer_info.channel = channel
        streamer_info.message = msg
        await send_success(
            ctx,
            f"Message de planning créé pour **{streamer}** dans {channel.mention}"
            f"{' et épinglé' if pin else ''}.",
        )

    @twitch_planning_setup.autocomplete("streamer")
    async def twitch_planning_setup_streamer_autocomplete(self, ctx: AutocompleteContext):
        if not ctx.guild:
            await ctx.send(choices=[])
            return
        guild_id = int(ctx.guild.id)
        query = (ctx.input_text or "").lower()
        choices = [
            {"name": s.streamer_id, "value": s.streamer_id}
            for s in self.streamers.values()
            if s.guild_id == guild_id and query in s.streamer_id.lower()
        ][:25]
        await ctx.send(choices=choices)


__all__ = ["StreamerInfo", "TwitchExtension"]
