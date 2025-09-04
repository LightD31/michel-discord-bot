"""
Extension Discord pour le Zevent - Version améliorée

"""

import os
import asyncio
from interactions import (
    Extension, Client, listen, Message, BaseChannel,
    Task, IntervalTrigger, Embed, File, TimestampStyles, utils, slash_command, SlashContext
)
from src import logutil
from src.utils import fetch, load_config
from datetime import datetime, timezone, timedelta, date
from typing import Dict, List, Optional, Any
from twitchAPI.twitch import Twitch
from dataclasses import dataclass

logger = logutil.init_logger(os.path.basename(__file__))
config, _, _ = load_config()

@dataclass
class StreamerInfo:
    display_name: str
    twitch_name: str
    is_online: bool
    location: str

def split_streamer_list(streamer_list: str, max_length: int = 1024) -> List[str]:
    chunks = []
    current_chunk = []
    current_length = 0
    for streamer in streamer_list.split(', '):
        if current_length + len(streamer) + 2 > max_length:  # +2 for ', '
            chunks.append(', '.join(current_chunk))
            current_chunk = [streamer]
            current_length = len(streamer)
        else:
            current_chunk.append(streamer)
            current_length += len(streamer) + 2

    if current_chunk:
        chunks.append(', '.join(current_chunk))

    return chunks

class Zevent(Extension):
    CHANNEL_ID = 993605590033117214
    MESSAGE_ID = 1399095553148850176
    API_URL = "https://zevent.fr/api/"
    PLANNING_API_URL = "https://zevent-api.gdoc.fr/events"
    STREAMERS_API_URL = "https://zevent-api.gdoc.fr/streamers"
    STREAMLABS_API_URL = "https://streamlabscharity.com/api/v1/teams/@zevent-2025/zevent-2025"
    UPDATE_INTERVAL = 900
    MILESTONE_INTERVAL = 100000  # 100k
    EVENT_START_DATE = datetime(2025, 9, 4, 17, 55, 0, tzinfo=timezone.utc)  # 4 septembre 2025 à 20h Paris (18h UTC) - Concert pré-événement
    MAIN_EVENT_START_DATE = datetime(2025, 9, 5, 16, 0, 0, tzinfo=timezone.utc)  # 5 septembre 2025 à 18h Paris (16h UTC) - Zevent principal

    def __init__(self, client: Client):
        self.client: Client = client
        self.channel: Optional[BaseChannel] = None
        self.message: Optional[Message] = None
        self.twitch: Optional[Twitch] = None
        self.last_milestone = 0
        self.last_data_cache: Optional[Dict] = None
        self.last_update_time: Optional[datetime] = None
        # Streamer name cache for resolving UUIDs -> display names
        self._streamer_cache = {}
        self._streamer_cache_time: Optional[datetime] = None
        self.STREAMER_CACHE_TTL = timedelta(hours=24)

    def _get_planning_day(self, now_date: date) -> str:
        """Return the planning day to request: use 2025-09-04 if current date is before that, else use today."""
        zevent_start = date(2025, 9, 4)
        target = zevent_start if now_date < zevent_start else now_date
        return target.strftime("%Y-%m-%d")

    async def _ensure_streamer_cache(self):
        """Fetch streamer mapping from STREAMERS_API_URL and cache it for STREAMER_CACHE_TTL."""
        try:
            if self._streamer_cache_time and datetime.now() - self._streamer_cache_time < self.STREAMER_CACHE_TTL:
                return

            data = await fetch(self.STREAMERS_API_URL, return_type="json")
            if not isinstance(data, list):
                logger.warning("Streamers API returned unexpected format; skipping cache update")
                return

            # Expected format: list of objects with 'id' and 'name' or similar
            mapping = {}
            for entry in data:
                try:
                    sid = entry.get('id')
                    pid = entry.get('participation_id') or entry.get('participationId')
                    name = entry.get('name') or entry.get('display_name') or entry.get('login')
                    if sid and name:
                        mapping[sid] = name
                    # Also map participation_id to the same name when available
                    if pid and name:
                        mapping[pid] = name
                except Exception:
                    continue

            if mapping:
                self._streamer_cache = mapping
                self._streamer_cache_time = datetime.now()
                logger.info(f"Streamer cache updated with {len(mapping)} entries")
        except Exception as e:
            logger.error(f"Failed to update streamer cache: {e}")

    def calculate_embed_size(self, embed: Embed) -> int:
        """Calculate the total character count of an embed"""
        size = 0
        
        if embed.title:
            size += len(embed.title)
        if embed.description:
            size += len(embed.description)
        if embed.footer and embed.footer.text:
            size += len(embed.footer.text)
        if embed.author and embed.author.name:
            size += len(embed.author.name)
        
        for field in embed.fields:
            if field.name:
                size += len(field.name)
            if field.value:
                size += len(field.value)
        
        return size

    def calculate_total_embeds_size(self, embeds: List[Embed]) -> int:
        """Calculate the total character count of all embeds"""
        return sum(self.calculate_embed_size(embed) for embed in embeds)

    def ensure_embeds_fit_limit(self, embeds: List[Embed], max_size: int = 5800) -> List[Embed]:
        """Ensure the total size of embeds doesn't exceed Discord's limit"""
        total_size = self.calculate_total_embeds_size(embeds)
        
        if total_size <= max_size:
            return embeds
        
        logger.warning(f"Embeds size ({total_size}) exceeds limit ({max_size}), reducing content")
        
        # Keep main embed and reduce others
        reduced_embeds = [embeds[0]]  # Always keep the main embed
        remaining_size = max_size - self.calculate_embed_size(embeds[0])
        
        for embed in embeds[1:]:
            embed_size = self.calculate_embed_size(embed)
            if embed_size <= remaining_size:
                reduced_embeds.append(embed)
                remaining_size -= embed_size
            else:
                # Try to reduce this embed by removing fields
                if embed.fields and remaining_size > 200:  # Keep at least some content
                    reduced_embed = Embed(
                        title=embed.title,
                        description=embed.description,
                        color=embed.color
                    )
                    if embed.footer and embed.footer.text:
                        reduced_embed.set_footer(embed.footer.text)
                    reduced_embed.timestamp = embed.timestamp
                    
                    # Add as many fields as possible
                    for field in embed.fields:
                        field_size = len(field.name or "") + len(field.value or "")
                        if field_size + 50 <= remaining_size:  # +50 for safety margin
                            reduced_embed.add_field(name=field.name, value=field.value, inline=field.inline)
                            remaining_size -= field_size
                        else:
                            break
                    
                    if reduced_embed.fields:  # Only add if we have at least one field
                        reduced_embeds.append(reduced_embed)
                break
        
        logger.info(f"Reduced embeds from {total_size} to {self.calculate_total_embeds_size(reduced_embeds)} characters")
        return reduced_embeds

    @listen()
    async def on_startup(self):
        try:
            self.channel = await self.client.fetch_channel(self.CHANNEL_ID)
            if hasattr(self.channel, 'fetch_message'):
                self.message = await self.channel.fetch_message(self.MESSAGE_ID)
            else:
                logger.error(f"Channel {self.CHANNEL_ID} does not support message fetching")
                return
            
            self.twitch = await Twitch(config["twitch"]["twitchClientId"], config["twitch"]["twitchClientSecret"])
            logger.info("Zevent extension initialized successfully")
            self.zevent.start()
            await self.zevent()
        except Exception as e:
            logger.error(f"Failed to initialize Zevent extension: {e}")

    def _validate_api_data(self, data: Any, data_type: str) -> bool:
        """Validate API response data structure"""
        try:
            if not isinstance(data, dict):
                return False
                
            if data_type == "zevent":
                required_keys = ["donationAmount", "live"]
                return all(key in data for key in required_keys)
            elif data_type == "planning":
                return "data" in data and isinstance(data["data"], list)
            elif data_type == "streamlabs":
                return "amount_raised" in data
            return False
        except Exception:
            return False

    def _safe_get_data(self, data: Any, key_path: List[str], default: Any = None) -> Any:
        """Safely navigate nested dictionary keys"""
        try:
            current = data
            for key in key_path:
                if isinstance(current, dict) and key in current:
                    current = current[key]
                else:
                    return default
            return current
        except Exception:
            return default

    def _is_event_started(self) -> bool:
        """Check if the Zevent has started (including pre-event concert)"""
        return datetime.now(timezone.utc) >= self.EVENT_START_DATE

    def _is_main_event_started(self) -> bool:
        """Check if the main Zevent has started (streamers go live)"""
        return datetime.now(timezone.utc) >= self.MAIN_EVENT_START_DATE

    async def _is_zevent_channel_live(self) -> bool:
        """Check if the main Zevent Twitch channel is currently live"""
        try:
            if not self.twitch:
                return False
            
            async for stream in self.twitch.get_streams(user_login=["zevent"]):
                return True  # If we find a stream, the channel is live
            return False  # No stream found
        except Exception as e:
            logger.error(f"Error checking if Zevent channel is live: {e}")
            return False

    async def _is_concert_active(self) -> bool:
        """Check if the concert is currently active (Zevent channel live but main event not started)"""
        if not self._is_event_started():
            return False  # Concert can't be active before the event start date
        if self._is_main_event_started():
            return False  # If main event started, we're past the concert phase
        
        # Check if Zevent channel is live (indicates concert is happening)
        return await self._is_zevent_channel_live()

    @Task.create(IntervalTrigger(seconds=UPDATE_INTERVAL))
    async def zevent(self):
        total_amount = "Données indisponibles"
        total_int = 0
        
        try:
            # Fetch data from all APIs concurrently
            logger.debug("Fetching data from APIs...")
            # Determine which day to request planning for.
            # If current date is before the Zevent start (2025-09-04),
            # show events for 2025-09-04 instead of the current date.
            now_date = datetime.now().date()
            target_day = self._get_planning_day(now_date)
            planning_url = f"{self.PLANNING_API_URL}?day={target_day}"
            
            data, planning_data, streamlabs_data = await asyncio.gather(
                fetch(self.API_URL, return_type="json"),
                fetch(planning_url, return_type="json"),
                fetch(self.STREAMLABS_API_URL, return_type="json"),
                return_exceptions=True
            )

            # Handle API fetch exceptions and validate data
            data = data if not isinstance(data, Exception) and self._validate_api_data(data, "zevent") else None
            planning_data = planning_data if not isinstance(planning_data, Exception) and isinstance(planning_data, list) else None
            streamlabs_data = streamlabs_data if not isinstance(streamlabs_data, Exception) and self._validate_api_data(streamlabs_data, "streamlabs") else None

            if isinstance(data, Exception):
                logger.error(f"Failed to fetch Zevent API: {data}")
            if isinstance(planning_data, Exception):
                logger.error(f"Failed to fetch Planning API: {planning_data}")
            if isinstance(streamlabs_data, Exception):
                logger.error(f"Failed to fetch Streamlabs API: {streamlabs_data}")

            # Check if event has started to determine what data to show
            concert_active = await self._is_concert_active()
            
            if not self._is_event_started():
                # Event hasn't started yet (before concert), show countdown and streamers list
                if data:
                    streams = await self.categorize_streams(self._safe_get_data(data, ["live"], []))
                    embeds = [
                        self.create_main_embed("0 €"),  # Countdown embed
                        self.create_location_embed("streamers présents sur place", streams["LAN"], withlink=False, viewers_count=None, total_count=streams.get("_totals", {}).get("LAN")),
                        self.create_location_embed("participants à distance", streams["Online"], withlink=False, viewers_count=None, total_count=streams.get("_totals", {}).get("Online")),
                    ]
                    
                    # Add top donations embed if donations are available
                    top_donations_embed = self.create_top_donations_embed(self._safe_get_data(data, ["live"], []))
                    if top_donations_embed:
                        embeds.append(top_donations_embed)
                    
                    # Add planning embed if available
                    if planning_data and isinstance(planning_data, list):
                        embeds.append(await self.create_planning_embed(planning_data))
                else:
                    # No data available, show only countdown
                    embeds = [self.create_main_embed("0 €")]
                
                # Ensure embeds fit Discord's size limit
                embeds = self.ensure_embeds_fit_limit(embeds)
                
                file = File("data/Zevent_logo.png")
                if self.message:
                    await self.message.edit(embeds=embeds, content="", files=[file])
                    logger.debug("Pre-event countdown message updated successfully")
                return
            elif concert_active or not self._is_main_event_started():
                # Concert phase: either Zevent channel is live OR we're in the concert time window
                if data:
                    total_amount, total_int = self.get_total_amount(data, streamlabs_data)
                    streams = await self.categorize_streams(self._safe_get_data(data, ["live"], []))
                    
                    # Determine the status for the main embed
                    main_embed_status = "concert_live" if concert_active else "concert_window"
                    embeds = [
                        self.create_main_embed(total_amount, concert_status=main_embed_status),
                        self.create_location_embed("streamers présents sur place", streams["LAN"], withlink=False, viewers_count=None, total_count=streams.get("_totals", {}).get("LAN")),
                        self.create_location_embed("participants à distance", streams["Online"], withlink=False, viewers_count=None, total_count=streams.get("_totals", {}).get("Online")),
                    ]
                    
                    # Add top donations embed
                    top_donations_embed = self.create_top_donations_embed(self._safe_get_data(data, ["live"], []))
                    if top_donations_embed:
                        embeds.append(top_donations_embed)
                    
                    # Add planning embed if available
                    if planning_data and isinstance(planning_data, list):
                        embeds.append(await self.create_planning_embed(planning_data))
                else:
                    embeds = [self.create_main_embed("Données indisponibles")]
                
                # Ensure embeds fit Discord's size limit
                embeds = self.ensure_embeds_fit_limit(embeds)
                
                file = File("data/Zevent_logo.png")
                if self.message:
                    await self.message.edit(embeds=embeds, content="", files=[file])
                    logger.debug("Concert phase message updated successfully")
                    
                await self.check_and_send_milestone(total_int if data else 0)
                return

            # If all APIs failed, try to use cached data or send error message
            if not data and not self.last_data_cache:
                logger.error("All APIs failed and no cached data available")
                await self.send_simplified_update(total_amount)
                return
            
            # Use cached data if current data is unavailable
            if not data and self.last_data_cache:
                logger.warning("Using cached data due to API failure")
                data = self.last_data_cache
            elif isinstance(data, dict):
                # Only cache valid dict responses
                self.last_data_cache = data
                self.last_update_time = datetime.now()

            if isinstance(data, dict):
                # Ensure streamlabs_data is either a dict or None
                if not isinstance(streamlabs_data, dict):
                    streamlabs_data = None

                total_amount, total_int = self.get_total_amount(data, streamlabs_data)
                streams = await self.categorize_streams(self._safe_get_data(data, ["live"], []))
                viewers_data = await self.get_viewers_by_location(self._safe_get_data(data, ["live"], []))

                embeds = [
                    self.create_main_embed(total_amount, viewers_data["Total"]),
                    self.create_location_embed("streamers présents sur place", streams["LAN"], withlink=False, viewers_count=viewers_data["LAN"], total_count=streams.get("_totals", {}).get("LAN")),
                    self.create_location_embed("participants à distance", streams["Online"], withlink=False, viewers_count=viewers_data["Online"], total_count=streams.get("_totals", {}).get("Online")),
                ]
                
                # Add top donations embed
                top_donations_embed = self.create_top_donations_embed(self._safe_get_data(data, ["live"], []))
                if top_donations_embed:
                    embeds.append(top_donations_embed)
                
                # Add planning embed only if planning data is available
                if planning_data and isinstance(planning_data, list):
                    embeds.append(await self.create_planning_embed(planning_data))

                # Ensure embeds fit Discord's size limit
                embeds = self.ensure_embeds_fit_limit(embeds)

                file = File("data/Zevent_logo.png")
                
                if self.message:
                    await self.message.edit(embeds=embeds, content="", files=[file])
                    logger.debug("Message updated successfully")

                await self.check_and_send_milestone(total_int)

        except Exception as e:
            logger.error(f"Unexpected error in zevent task: {e}")
            await self.send_simplified_update(total_amount)

    def get_total_amount(self, data: Dict, streamlabs_data: Optional[Dict]) -> tuple[str, float]:
        """Get total amount from Zevent and Streamlabs APIs, using the higher value"""
        try:
            total_amount = self._safe_get_data(data, ["donationAmount", "formatted"], "0 €")
            total_int = float(self._safe_get_data(data, ["donationAmount", "number"], 0))
            
            if streamlabs_data and "amount_raised" in streamlabs_data:
                total_from_streamlabs = streamlabs_data["amount_raised"] / 100  # Streamlabs API returns amount in cents
                logger.debug(f"Total from Zevent: {total_int}, Total from Streamlabs: {total_from_streamlabs}")
                if total_from_streamlabs > total_int:
                    total_int = total_from_streamlabs
                    total_amount = f"{total_int:,.2f} €".replace(",", " ")
            
            return total_amount, total_int
        except Exception as e:
            logger.error(f"Error calculating total amount: {e}")
            return "Erreur de calcul", 0.0

    async def check_and_send_milestone(self, total_amount: float):
        current_milestone = int(total_amount // self.MILESTONE_INTERVAL * self.MILESTONE_INTERVAL)

        if current_milestone > self.last_milestone:
            # Doesn't send message for the first milestone to avoid spam when bot starts
            if self.last_milestone != 0:
                milestone_message = f"🎉 Nouveau palier atteint : {current_milestone:,} € récoltés ! 🎉".replace(",", " ")
                if self.channel and hasattr(self.channel, 'send'):
                    await self.channel.send(milestone_message)
                else:
                    logger.error("Cannot send milestone message: channel not available or doesn't support sending")
            self.last_milestone = current_milestone

    async def send_simplified_update(self, total_amount: str):
        try:
            simple_embed = Embed(
                title="Zevent Update",
                description=f"Total récolté: {total_amount}\n\nDésolé, nous rencontrons des difficultés techniques pour afficher les détails des streamers.",
                color=0x59af37,
            )
            simple_embed.timestamp = utils.timestamp_converter(datetime.now())
            
            if self.message:
                await self.message.edit(embeds=[simple_embed], content="")
        except Exception as e:
            logger.error(f"Failed to send simplified update: {e}")

    async def categorize_streams(self, streams: List[Dict]) -> Dict[str, Dict[str, StreamerInfo]]:
        categorized = {"LAN": {}, "Online": {}, "_totals": {"LAN": 0, "Online": 0}}
        
        if not streams or not self.twitch:
            return categorized
        
        try:
            twitch_usernames = list(set(stream.get("twitch", "") for stream in streams if stream.get("twitch")))
            
            batch_size = 100
            live_streamers = set()
            user_ids = {}  # Store user IDs from stream data
            
            for i in range(0, len(twitch_usernames), batch_size):
                batch = twitch_usernames[i:i+batch_size]
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
                # Count total participants
                categorized["_totals"][location] += 1
            
            # Limit online participants to top 100
            if "Online" in categorized:
                online_streamers = list(categorized["Online"].values())
                live_online = [s for s in online_streamers if s.is_online]
                
                # If we have fewer than 100 live online streamers, fill with top followers
                if len(live_online) < 100:
                    offline_online = [s for s in online_streamers if not s.is_online]
                    # Get follower counts for offline streamers
                    offline_with_followers = await self._get_streamers_with_followers(offline_online, user_ids)
                    # Sort by follower count and take what we need
                    needed = 100 - len(live_online)
                    top_offline = offline_with_followers[:needed]
                    
                    # Rebuild the online category with live + top offline
                    selected_streamers = live_online + top_offline
                else:
                    # Just take the first 100 live streamers
                    selected_streamers = live_online[:100]
                
                # Rebuild the Online category (but keep the total count)
                categorized["Online"] = {s.display_name: s for s in selected_streamers}
                
        except Exception as e:
            logger.error(f"Error categorizing streams: {e}")
        
        return categorized
    
    async def _get_streamers_with_followers(self, streamers: List[StreamerInfo], user_ids: Dict[str, str]) -> List[StreamerInfo]:
        """Get follower counts for offline streamers and return them sorted by follower count"""
        streamers_with_counts = []
        
        try:
            # Get follower counts for offline streamers using existing user IDs
            for streamer in streamers:
                try:
                    user_id = user_ids.get(streamer.twitch_name.lower())
                    if user_id:
                        followers = await self.twitch.get_channel_followers(broadcaster_id=user_id)
                        follower_count = followers.total if hasattr(followers, 'total') else 0
                        streamers_with_counts.append((streamer, follower_count))
                    else:
                        # If no user ID available, get it from get_users as fallback
                        users = await self.twitch.get_users(logins=[streamer.twitch_name])
                        user_list = [user async for user in users]
                        if user_list:
                            followers = await self.twitch.get_channel_followers(broadcaster_id=user_list[0].id)
                            follower_count = followers.total if hasattr(followers, 'total') else 0
                            streamers_with_counts.append((streamer, follower_count))
                        else:
                            streamers_with_counts.append((streamer, 0))
                except Exception as e:
                    logger.debug(f"Failed to get followers for {streamer.twitch_name}: {e}")
                    streamers_with_counts.append((streamer, 0))
            
            # Sort by follower count (descending)
            streamers_with_counts.sort(key=lambda x: x[1], reverse=True)
            return [streamer for streamer, _ in streamers_with_counts]
            
        except Exception as e:
            logger.error(f"Error getting streamers with followers: {e}")
            return streamers

    async def get_total_viewers_from_twitch(self, streams: List[Dict]) -> str:
        """Get total viewer count from Twitch API for all live streams"""
        try:
            if not streams or not self.twitch:
                return "N/A"
            
            twitch_usernames = list(set(stream.get("twitch", "") for stream in streams if stream.get("twitch")))
            total_viewers = 0
            
            batch_size = 100
            for i in range(0, len(twitch_usernames), batch_size):
                batch = twitch_usernames[i:i+batch_size]
                async for stream in self.twitch.get_streams(user_login=batch):
                    total_viewers += stream.viewer_count
            
            # Format the number with spaces as thousands separators
            return f"{total_viewers:,}".replace(",", " ")
        except Exception as e:
            logger.error(f"Error getting total viewers from Twitch: {e}")
            return "N/A"

    async def get_viewers_by_location(self, streams: List[Dict]) -> Dict[str, str]:
        """Get viewer count from Twitch API separated by location (LAN/Online)"""
        try:
            if not streams or not self.twitch:
                return {"LAN": "N/A", "Online": "N/A", "Total": "N/A"}
            
            # Organize streams by location
            streams_by_location = {"LAN": [], "Online": []}
            for stream in streams:
                location = stream.get("location", "Online")
                twitch_name = stream.get("twitch", "")
                if twitch_name:
                    streams_by_location[location].append(twitch_name)
            
            # Get live streams data from Twitch
            all_twitch_usernames = list(set(stream.get("twitch", "") for stream in streams if stream.get("twitch")))
            live_streams_data = {}
            
            batch_size = 100
            for i in range(0, len(all_twitch_usernames), batch_size):
                batch = all_twitch_usernames[i:i+batch_size]
                async for stream in self.twitch.get_streams(user_login=batch):
                    live_streams_data[stream.user_login.lower()] = stream.viewer_count
            
            # Calculate viewers by location
            viewers_by_location = {"LAN": 0, "Online": 0}
            for location, streamers in streams_by_location.items():
                for streamer in streamers:
                    if streamer.lower() in live_streams_data:
                        viewers_by_location[location] += live_streams_data[streamer.lower()]
            
            total_viewers = viewers_by_location["LAN"] + viewers_by_location["Online"]
            
            # Format the numbers with spaces as thousands separators
            return {
                "LAN": f"{viewers_by_location['LAN']:,}".replace(",", " "),
                "Online": f"{viewers_by_location['Online']:,}".replace(",", " "),
                "Total": f"{total_viewers:,}".replace(",", " ")
            }
        except Exception as e:
            logger.error(f"Error getting viewers by location: {e}")
            return {"LAN": "N/A", "Online": "N/A", "Total": "N/A"}

    def create_main_embed(self, total_amount: str, nombre_viewers: Optional[str] = None, finished: bool = False, concert_status: Optional[str] = None) -> Embed:
        embed = Embed(
            title="Zevent 2025",
            color=0x59af37,
        )
        
        if finished:
            embed.description = f"Total récolté: {total_amount}"
        elif not self._is_event_started():
            # Event hasn't started yet, show countdown to concert
            event_timestamp = utils.timestamp_converter(self.EVENT_START_DATE)
            embed.description = (f"🕒 Le concert pré-événement commence {event_timestamp.format(TimestampStyles.RelativeTime)}\n\n"
                               f"📅 Concert : {event_timestamp.format(TimestampStyles.LongDateTime)}\n"
                               f"📅 Zevent : {utils.timestamp_converter(self.MAIN_EVENT_START_DATE).format(TimestampStyles.LongDateTime)}")
        elif concert_status == "concert_live":
            # Concert is currently live (Zevent channel detected online)
            main_event_timestamp = utils.timestamp_converter(self.MAIN_EVENT_START_DATE)
            embed.description = (f"🎵 **Concert en direct !** 🔴\n"
                               f"Total récolté : {total_amount}\n\n"
                               f"▶️ [Regarder sur Twitch](https://www.twitch.tv/zevent)\n\n"
                               f"🕒 Le Zevent commence {main_event_timestamp.format(TimestampStyles.RelativeTime)}\n"
                               f"📅 Début du marathon: {main_event_timestamp.format(TimestampStyles.LongDateTime)}")
        elif not self._is_main_event_started():
            # Concert phase but not currently live - show like pre-event but with total amount
            main_event_timestamp = utils.timestamp_converter(self.MAIN_EVENT_START_DATE)
            embed.description = (f"🕒 Le Zevent commence {main_event_timestamp.format(TimestampStyles.RelativeTime)}\n\n"
                               f"📅 Début du marathon: {main_event_timestamp.format(TimestampStyles.LongDateTime)}\n\n"
                               f"💰 Total récolté: {total_amount}")
        else:
            embed.description = f"Total récolté: {total_amount}\nViewers cumulés: {nombre_viewers or 'N/A'}"
            
        embed.timestamp = utils.timestamp_converter(datetime.now())
        
        # Set thumbnail and footer using the proper methods
        embed.set_thumbnail("attachment://Zevent_logo.png")
        embed.set_footer("Source: zevent.fr / Twitch ❤️")
        
        return embed

    def create_location_embed(self, title: str, streams: Dict[str, StreamerInfo], withlink=True, finished=False, viewers_count: Optional[str] = None, total_count: Optional[int] = None) -> Embed:
        displayed_count = len(streams)
        actual_count = total_count if total_count is not None else displayed_count
        
        # For online participants, show actual count vs displayed count if different
        if "distance" in title and actual_count > displayed_count and not finished:
            embed_title = f"Top {displayed_count}/{actual_count} {title}"
        else:
            embed_title = f"Les {actual_count} {title}"
            
        embed = Embed(
            title=embed_title,
            color=0x59af37,
        )
        
        # Add viewer count to description if provided and event has started
        if viewers_count and not finished and self._is_event_started():
            embed.description = f"Viewers: {viewers_count}"
        
        embed.set_footer("Source: zevent.fr / Twitch ❤️")
        embed.timestamp = utils.timestamp_converter(datetime.now())
        
        if finished:
            online_streamers = list(streams.values())
            offline_streamers = []
            status = f"Les {actual_count} {title}"  # Use the embed title as the field title when finished
            withlink = False  # Disable links when the event is finished
        elif not self._is_event_started():
            # Event hasn't started yet, show all streamers without online/offline status
            all_streamers = list(streams.values())
            offline_streamers = []
            status = f"Les {actual_count} {title}"
            online_streamers = all_streamers
        else:
            online_streamers = [s for s in streams.values() if s.is_online]
            offline_streamers = [s for s in streams.values() if not s.is_online]
            status = "Streamers en ligne"

        for stream_status, streamers in [(status, online_streamers), ("Hors-ligne", offline_streamers)]:
            if not streamers:
                continue

            streamer_list = ', '.join(
                f"[{s.display_name}](https://www.twitch.tv/{s.twitch_name})" if withlink else s.display_name.replace("_", "\\_")
                for s in streamers
            )

            chunks = split_streamer_list(streamer_list, max_length=1024)
            for i, chunk in enumerate(chunks, 1):
                field_name = stream_status if len(chunks) == 1 else f"{stream_status} {i}/{len(chunks)}"
                embed.add_field(name=field_name, value=chunk or "Aucun streamer", inline=True)

        if len(embed.fields) == 0:
            embed.add_field(name="Status", value="Aucun streamer en ce moment", inline=False)

        return embed


    async def create_planning_embed(self, events: List[Dict]) -> Embed:
        embed = Embed(title="Prochains évènements", color=0x59af37)
        embed.set_footer("Source: zevent.gdoc.fr ❤️")
        embed.timestamp = utils.timestamp_converter(datetime.now())

        # New planning format: start_date, end_date, participants: {host: [...], participant: [...]}
        sorted_events = sorted(events, key=lambda x: x.get('start_date') or '')

        # Ensure we have streamer names cached for UUID resolution
        await self._ensure_streamer_cache()

        for event in sorted_events:
            try:
                start_at = event.get('start_date') or ''
                finished_at = event.get('end_date') or ''

                if not start_at or not finished_at:
                    continue

                start_time = datetime.fromisoformat(start_at.replace('Z', '+00:00')).replace(tzinfo=timezone.utc)
                end_time = datetime.fromisoformat(finished_at.replace('Z', '+00:00')).replace(tzinfo=timezone.utc)

                field_name = event.get('name', 'Événement')

                duration = end_time - start_time

                time_str = (f"{str(utils.timestamp_converter(start_time)).format(TimestampStyles.LongDateTime)} - "
                            f"{str(utils.timestamp_converter(end_time)).format(TimestampStyles.ShortTime)}"
                            if duration >= timedelta(minutes=20)
                            else f"{str(utils.timestamp_converter(start_time)).format(TimestampStyles.LongDateTime)}")

                field_value = f"{time_str}\n"

                if event.get('description'):
                    field_value += f"{event['description']}\n"

                participants = event.get('participants') or {}

                # Hosts
                hosts_names = []
                for hid in participants.get('host', []):
                    name = self._streamer_cache.get(hid) or hid
                    hosts_names.append(name.replace("_", "\\_"))

                if hosts_names:
                    field_value += f"Hosts: {', '.join(hosts_names)}\n"

                # Participants
                part_names = []
                for pid in participants.get('participant', []):
                    name = self._streamer_cache.get(pid) or pid
                    part_names.append(name.replace("_", "\\_"))

                # To avoid too long field, show up to 20 names then a count
                if part_names:
                    if len(part_names) > 20:
                        shown = ', '.join(part_names[:20])
                        field_value += f"Participants ({len(part_names)}): {shown}..."
                    else:
                        field_value += f"Participants: {', '.join(part_names)}"

                embed.add_field(name=field_name, value=field_value, inline=True)
            except Exception as e:
                logger.error(f"Error processing event: {e}")

        return embed
    
    def create_top_donations_embed(self, streams: List[Dict]) -> Optional[Embed]:
        """Create embed showing top streamers by donation amount"""
        try:
            if not streams:
                return None
            
            # Filter and sort streamers by donation amount
            streamers_with_donations = []
            for stream in streams:
                donation_amount = self._safe_get_data(stream, ["donationAmount", "number"], 0)
                if donation_amount > 0:  # Only include streamers with donations
                    streamers_with_donations.append({
                        "display": stream.get("display", "Unknown"),
                        "donation_amount": donation_amount,
                        "donation_formatted": self._safe_get_data(stream, ["donationAmount", "formatted"], "0 €"),
                        "twitch": stream.get("twitch", "")
                    })
            
            # Sort by donation amount (descending)
            top_streamers = sorted(streamers_with_donations, key=lambda x: x["donation_amount"], reverse=True)
            
            if not top_streamers:
                return None
            
            embed = Embed(
                title="🏆 Top Donations par streamer",
                color=0xffd700,  # Gold color
            )
            embed.set_footer("Source: zevent.fr ❤️")
            embed.timestamp = utils.timestamp_converter(datetime.now())
            
            # Create the leaderboard with dynamic limiting
            leaderboard_text = ""
            max_streamers = 20  # Start with 20, but reduce if needed
            
            for attempt in range(3):  # Try up to 3 times with fewer streamers
                leaderboard_text = ""
                current_top = top_streamers[:max_streamers]
                
                for i, streamer in enumerate(current_top, 1):
                    # Use medals for top 3
                    if i == 1:
                        medal = "🥇"
                    elif i == 2:
                        medal = "🥈"
                    elif i == 3:
                        medal = "🥉"
                    else:
                        medal = f"{i}."
                    
                    display_name = streamer["display"].replace("_", "\\_")
                    leaderboard_text += f"{medal} **{display_name}** - {streamer['donation_formatted']}\n"
                
                # Check if the text fits in a single field (1024 char limit)
                if len(leaderboard_text) <= 1000:  # Leave some margin
                    break
                
                # Reduce number of streamers and try again
                max_streamers = max(10, max_streamers - 5)
            
            # Add the field
            embed.add_field(name="Top donations", value=leaderboard_text, inline=False)
            
            return embed
            
        except Exception as e:
            logger.error(f"Error creating top donations embed: {e}")
            return None
    
    @slash_command(name="zevent_finish", description="Créée l'embed final après l'évènement")
    async def end(self, ctx: SlashContext):
        try:
            # Fetch the data
            data = await fetch(self.API_URL, return_type="json")
            if not data or not self._validate_api_data(data, "zevent"):
                await ctx.send("Erreur: Impossible de récupérer les données du Zevent", ephemeral=True)
                return
                
            total_amount = self._safe_get_data(data, ["donationAmount", "formatted"], "Données indisponibles")
            streams = await self.categorize_streams(self._safe_get_data(data, ["live"], []))
            
            # Create the embeds with all the streamers regardless of if they are online or offline, no planning
            embeds = [ 
                self.create_main_embed(total_amount, finished=True),
                self.create_location_embed("streamers présents sur place", streams["LAN"], finished=True, withlink=False, total_count=streams.get("_totals", {}).get("LAN")),
                self.create_location_embed("participants à distance", streams["Online"], finished=True, withlink=False, total_count=streams.get("_totals", {}).get("Online"))
            ]
            
            # Ensure embeds fit Discord's size limit
            embeds = self.ensure_embeds_fit_limit(embeds)
            
            # Edit the message
            if self.message:
                await self.message.edit(embeds=embeds, content="")
                await ctx.send("Embed final créé avec succès", ephemeral=True)
            else:
                await ctx.send("Erreur: Message non trouvé", ephemeral=True)
        except Exception as e:
            logger.error(f"Error in zevent_finish command: {e}")
            await ctx.send("Erreur lors de la création de l'embed final", ephemeral=True)
        
        
        
        