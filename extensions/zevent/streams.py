"""Twitch API helpers: categorise streamers by location and count viewers."""

import os

from features.zevent.stats import LAN, ONLINE, resolve_location
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
        categorized = {LAN: {}, ONLINE: {}, "_totals": {LAN: 0, ONLINE: 0}}

        if not streams or not self.twitch:
            return categorized

        # zevent.fr no longer ships a ``location`` on its live entries; the
        # LAN/remote split comes from the stats API, keyed by Twitch login/id.
        await self._ensure_participant_cache()

        try:
            twitch_usernames = list(
                {stream.get("twitch", "") for stream in streams if stream.get("twitch")}
            )

            batch_size = 100
            live_streamers = set()

            for i in range(0, len(twitch_usernames), batch_size):
                batch = twitch_usernames[i : i + batch_size]
                async for stream in self.twitch.get_streams(user_login=batch):
                    live_streamers.add(stream.user_login.lower())

            for stream in streams:
                location = resolve_location(stream, self._location_index)
                twitch_name = stream.get("twitch", "").lower()
                display_name = stream.get("display", "Unknown")
                is_online = twitch_name in live_streamers
                donation_amount = self._safe_get_data(stream, ["donationAmount", "number"], 0) or 0

                streamer_info = StreamerInfo(
                    display_name, twitch_name, is_online, location, float(donation_amount)
                )
                categorized[location][display_name] = streamer_info
                categorized["_totals"][location] += 1

            if ONLINE in categorized:
                online_streamers = list(categorized[ONLINE].values())
                live_online = [s for s in online_streamers if s.is_online]

                if len(live_online) < 100:
                    # Fill the remaining slots with the biggest fundraisers.
                    # This used to rank by Twitch follower count, which cost two
                    # sequential API calls per offline streamer (~400 per refresh
                    # for a full remote roster) — more than the refresh interval
                    # allows. The donation total already rides along in the
                    # zevent.fr payload and is the more meaningful order here.
                    # Name breaks ties so the selection stays stable across
                    # refreshes: before the event nobody has raised anything,
                    # and API ordering alone would churn the embed.
                    offline_online = sorted(
                        (s for s in online_streamers if not s.is_online),
                        key=lambda s: (-s.donation_amount, s.display_name.lower()),
                    )
                    needed = 100 - len(live_online)
                    selected_streamers = live_online + offline_online[:needed]
                else:
                    selected_streamers = live_online[:100]

                categorized[ONLINE] = {s.display_name: s for s in selected_streamers}

        except Exception as e:
            logger.error(f"Error categorizing streams: {e}")

        return categorized

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

            await self._ensure_participant_cache()

            streams_by_location: dict[str, list[str]] = {LAN: [], ONLINE: []}
            for stream in streams:
                location = resolve_location(stream, self._location_index)
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
