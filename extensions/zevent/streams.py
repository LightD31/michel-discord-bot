"""Twitch API helpers: categorise streamers, count viewers, sort by followers."""

import inspect
import os

from src.core import logging as logutil

from ._common import StreamerInfo

logger = logutil.init_logger(os.path.basename(__file__))


class StreamsMixin:
    """Resolve stream status via the Twitch API and aggregate viewer counts."""

    def _get_stream_total_count(self, streams: dict, location: str) -> int:
        totals = streams.get("_totals", {})
        if isinstance(totals, dict):
            count = totals.get(location, 0)
            return count if isinstance(count, int) else 0
        return 0

    async def categorize_streams(self, streams: list[dict]) -> dict[str, dict[str, StreamerInfo]]:
        categorized = {"LAN": {}, "Online": {}, "_totals": {"LAN": 0, "Online": 0}}

        if not streams or not self.twitch:
            return categorized

        try:
            twitch_usernames = list(
                {stream.get("twitch", "") for stream in streams if stream.get("twitch")}
            )

            batch_size = 100
            live_streamers = set()
            user_ids = {}

            for i in range(0, len(twitch_usernames), batch_size):
                batch = twitch_usernames[i : i + batch_size]
                async for stream in self.twitch.get_streams(user_login=batch):
                    live_streamers.add(stream.user_login.lower())
                    user_ids[stream.user_login.lower()] = stream.user_id

            for stream in streams:
                location = stream.get("location", "Online")
                twitch_name = stream.get("twitch", "").lower()
                display_name = stream.get("display", "Unknown")
                is_online = twitch_name in live_streamers

                streamer_info = StreamerInfo(display_name, twitch_name, is_online, location)
                categorized[location][display_name] = streamer_info
                categorized["_totals"][location] += 1

            if "Online" in categorized:
                online_streamers = list(categorized["Online"].values())
                live_online = [s for s in online_streamers if s.is_online]

                if len(live_online) < 100:
                    offline_online = [s for s in online_streamers if not s.is_online]
                    offline_with_followers = await self._get_streamers_with_followers(
                        offline_online, user_ids
                    )
                    needed = 100 - len(live_online)
                    top_offline = offline_with_followers[:needed]
                    selected_streamers = live_online + top_offline
                else:
                    selected_streamers = live_online[:100]

                categorized["Online"] = {s.display_name: s for s in selected_streamers}

        except Exception as e:
            logger.error(f"Error categorizing streams: {e}")

        return categorized

    async def _follower_count(self, broadcaster_id: str) -> int:
        """Return the follower count for ``broadcaster_id``.

        twitchAPI 4.x returns a ``ChannelFollowersResult`` with ``.total``; older
        versions returned an async generator with no cheap total. Handle both.
        """
        call = self.twitch.get_channel_followers(broadcaster_id=broadcaster_id, first=1)
        if inspect.isasyncgen(call):
            count = 0
            try:
                async for _ in call:
                    count += 1
            except Exception:
                pass
            return count
        result = await call
        return int(getattr(result, "total", 0) or 0)

    async def _get_streamers_with_followers(
        self, streamers: list[StreamerInfo], user_ids: dict[str, str]
    ) -> list[StreamerInfo]:
        streamers_with_counts = []

        try:
            for streamer in streamers:
                try:
                    user_id = user_ids.get(streamer.twitch_name.lower())
                    if user_id:
                        follower_count = await self._follower_count(user_id)
                        streamers_with_counts.append((streamer, follower_count))
                    else:
                        user_list = [
                            user
                            async for user in self.twitch.get_users(logins=[streamer.twitch_name])
                        ]
                        if user_list:
                            follower_count = await self._follower_count(user_list[0].id)
                            streamers_with_counts.append((streamer, follower_count))
                        else:
                            streamers_with_counts.append((streamer, 0))
                except Exception as e:
                    logger.debug(f"Failed to get followers for {streamer.twitch_name}: {e}")
                    streamers_with_counts.append((streamer, 0))

            streamers_with_counts.sort(key=lambda x: x[1], reverse=True)
            return [streamer for streamer, _ in streamers_with_counts]
        except Exception as e:
            logger.error(f"Error getting streamers with followers: {e}")
            return streamers

    async def get_total_viewers_from_twitch(self, streams: list[dict]) -> str:
        """Cumulative viewer count across all live streams, formatted with spaces."""
        try:
            if not streams or not self.twitch:
                return "N/A"

            twitch_usernames = list(
                {stream.get("twitch", "") for stream in streams if stream.get("twitch")}
            )
            total_viewers = 0

            batch_size = 100
            for i in range(0, len(twitch_usernames), batch_size):
                batch = twitch_usernames[i : i + batch_size]
                async for stream in self.twitch.get_streams(user_login=batch):
                    total_viewers += stream.viewer_count

            return f"{total_viewers:,}".replace(",", " ")
        except Exception as e:
            logger.error(f"Error getting total viewers from Twitch: {e}")
            return "N/A"

    async def get_viewers_by_location(self, streams: list[dict]) -> dict[str, str]:
        """Viewer counts split between LAN / Online participants."""
        try:
            if not streams or not self.twitch:
                return {"LAN": "N/A", "Online": "N/A", "Total": "N/A"}

            streams_by_location = {"LAN": [], "Online": []}
            for stream in streams:
                location = stream.get("location", "Online")
                twitch_name = stream.get("twitch", "")
                if twitch_name:
                    streams_by_location[location].append(twitch_name)

            all_twitch_usernames = list(
                {stream.get("twitch", "") for stream in streams if stream.get("twitch")}
            )
            live_streams_data = {}

            batch_size = 100
            for i in range(0, len(all_twitch_usernames), batch_size):
                batch = all_twitch_usernames[i : i + batch_size]
                async for stream in self.twitch.get_streams(user_login=batch):
                    live_streams_data[stream.user_login.lower()] = stream.viewer_count

            viewers_by_location = {"LAN": 0, "Online": 0}
            for location, streamers in streams_by_location.items():
                for streamer in streamers:
                    if streamer.lower() in live_streams_data:
                        viewers_by_location[location] += live_streams_data[streamer.lower()]

            total_viewers = viewers_by_location["LAN"] + viewers_by_location["Online"]

            return {
                "LAN": f"{viewers_by_location['LAN']:,}".replace(",", " "),
                "Online": f"{viewers_by_location['Online']:,}".replace(",", " "),
                "Total": f"{total_viewers:,}".replace(",", " "),
            }
        except Exception as e:
            logger.error(f"Error getting viewers by location: {e}")
            return {"LAN": "N/A", "Online": "N/A", "Total": "N/A"}
