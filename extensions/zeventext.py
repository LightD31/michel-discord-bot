"""
Extension Discord pour le Zevent - Version améliorée

Améliorations apportées:
1. Gestion d'erreurs robuste avec cache de secours
2. Validation des données API avec fonctions d'aide sécurisées
3. Meilleure gestion des exceptions lors des appels API concurrents
4. Code plus maintenable avec séparation des responsabilités
5. Gestion améliorée des embeds avec méthodes utilitaires
6. Logs plus informatifs pour le debugging
7. Protection contre les erreurs de type avec validation des données
8. Cache des données pour éviter les pannes lors d'interruptions API
9. Amélioration de la méthode de fin d'événement avec gestion d'erreurs
10. Gestion robuste des milestones avec vérification des capacités de canal
"""

import os
import asyncio
from interactions import (
    Extension, Client, listen, Message, BaseChannel,
    Task, IntervalTrigger, Embed, File, TimestampStyles, utils, slash_command, SlashContext
)
from src import logutil
from src.utils import fetch, load_config
from datetime import datetime, timezone, timedelta
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
    PLANNING_API_URL = "https://api.zevent.gdoc.fr/events/upcoming"
    STREAMLABS_API_URL = "https://streamlabscharity.com/api/v1/teams/@zevent-2025/zevent-2025"
    UPDATE_INTERVAL = 900
    MILESTONE_INTERVAL = 100000  # 100k

    def __init__(self, client: Client):
        self.client: Client = client
        self.channel: Optional[BaseChannel] = None
        self.message: Optional[Message] = None
        self.twitch: Optional[Twitch] = None
        self.last_milestone = 0
        self.last_data_cache: Optional[Dict] = None
        self.last_update_time: Optional[datetime] = None

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

    @Task.create(IntervalTrigger(seconds=UPDATE_INTERVAL))
    async def zevent(self):
        total_amount = "Données indisponibles"
        total_int = 0
        
        try:
            # Fetch data from all APIs concurrently
            logger.debug("Fetching data from APIs...")
            data, streamlabs_data = await asyncio.gather(
                fetch(self.API_URL, return_type="json"),
                # fetch(self.PLANNING_API_URL, return_type="json"),  # Planning API not available yet
                fetch(self.STREAMLABS_API_URL, return_type="json"),
                return_exceptions=True
            )
            planning_data = None  # Planning API not available yet

            # Handle API fetch exceptions and validate data
            data = data if not isinstance(data, Exception) and self._validate_api_data(data, "zevent") else None
            # planning_data = planning_data if not isinstance(planning_data, Exception) and self._validate_api_data(planning_data, "planning") else None  # Planning API not available yet
            streamlabs_data = streamlabs_data if not isinstance(streamlabs_data, Exception) and self._validate_api_data(streamlabs_data, "streamlabs") else None

            if isinstance(data, Exception):
                logger.error(f"Failed to fetch Zevent API: {data}")
            # if isinstance(planning_data, Exception):  # Planning API not available yet
            #     logger.error(f"Failed to fetch Planning API: {planning_data}")
            if isinstance(streamlabs_data, Exception):
                logger.error(f"Failed to fetch Streamlabs API: {streamlabs_data}")

            # If all APIs failed, try to use cached data or send error message
            if not data and not self.last_data_cache:
                logger.error("All APIs failed and no cached data available")
                await self.send_simplified_update(total_amount)
                return
            
            # Use cached data if current data is unavailable
            if not data and self.last_data_cache:
                logger.warning("Using cached data due to API failure")
                data = self.last_data_cache
            elif data:
                self.last_data_cache = data
                self.last_update_time = datetime.now()

            if data:
                total_amount, total_int = self.get_total_amount(data, streamlabs_data)
                streams = await self.categorize_streams(self._safe_get_data(data, ["live"], []))
                nombre_viewers = await self.get_total_viewers_from_twitch(self._safe_get_data(data, ["live"], []))

                embeds = [
                    self.create_main_embed(total_amount, nombre_viewers),
                    self.create_location_embed("streamers présents sur place", streams["LAN"]),
                    self.create_location_embed("participants à distance", streams["Online"], withlink=False),
                ]
                
                # Add planning embed only if planning data is available
                if planning_data and isinstance(planning_data, dict) and "data" in planning_data:
                    embeds.append(self.create_planning_embed(planning_data["data"]))

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
        categorized = {"LAN": {}, "Online": {}}
        
        if not streams or not self.twitch:
            return categorized
        
        try:
            twitch_usernames = list(set(stream.get("twitch", "") for stream in streams if stream.get("twitch")))
            
            batch_size = 100
            live_streamers = set()
            
            for i in range(0, len(twitch_usernames), batch_size):
                batch = twitch_usernames[i:i+batch_size]
                async for stream in self.twitch.get_streams(user_login=batch):
                    live_streamers.add(stream.user_login.lower())

            for stream in streams:
                location = stream.get("location", "Online")
                twitch_name = stream.get("twitch", "").lower()
                display_name = stream.get("display", "Unknown")
                is_online = twitch_name in live_streamers
                
                streamer_info = StreamerInfo(display_name, twitch_name, is_online, location)
                categorized[location][display_name] = streamer_info
        except Exception as e:
            logger.error(f"Error categorizing streams: {e}")
        
        return categorized

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

    def create_main_embed(self, total_amount: str, nombre_viewers: Optional[str] = None, finished: bool = False) -> Embed:
        embed = Embed(
            title="Zevent 2025",
            color=0x59af37,
        )
        
        if finished:
            embed.description = f"Total récolté: {total_amount}"
        else:
            embed.description = f"Total récolté: {total_amount}\nViewers cumulés: {nombre_viewers or 'N/A'}"
            embed.timestamp = utils.timestamp_converter(datetime.now())
        
        # Set thumbnail and footer using the proper methods
        embed.set_thumbnail("attachment://Zevent_logo.png")
        embed.set_footer("Source: zevent.fr ❤️")
        
        return embed

    def create_location_embed(self, title: str, streams: Dict[str, StreamerInfo], withlink=True, finished=False) -> Embed:
        streamer_count = len(streams)
        embed = Embed(
            title=f"Les {streamer_count} {title}",
            color=0x59af37,
        )
        embed.set_footer("Source: zevent.fr / Twitch ❤️")
        embed.timestamp = utils.timestamp_converter(datetime.now())
        
        if finished:
            online_streamers = list(streams.values())
            offline_streamers = []
            status = f"Les {streamer_count} {title}"  # Use the embed title as the field title when finished
            withlink = False  # Disable links when the event is finished
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


    def create_planning_embed(self, events: List[Dict]) -> Embed:
        embed = Embed(title="Prochains évènements", color=0x59af37)
        embed.set_footer("Source: zevent.gdoc.fr ❤️")
        embed.timestamp = utils.timestamp_converter(datetime.now())
        
        sorted_events = sorted(events, key=lambda x: x.get('start_at', ''))
        
        for event in sorted_events:
            try:
                start_at = event.get('start_at', '')
                finished_at = event.get('finished_at', '')
                
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
                
                if event.get('hosts'):
                    hosts = ', '.join([host.get('name', '').replace("_", "\\_") for host in event['hosts']])
                    field_value += f"Hosts: {hosts}\n"
                
                if event.get('participants'):
                    participants = ', '.join([participant.get('name', '').replace("_", "\\_") for participant in event['participants']])
                    
                    # Limit the number of participants to 1024 characters
                    if len(participants) > 1024:
                        participants = participants[:1021] + '...'
                    
                    field_value += f"Participants: {participants}"
                
                embed.add_field(name=field_name, value=field_value, inline=True)
            except Exception as e:
                logger.error(f"Error processing event: {e}")
        
        return embed
    
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
                self.create_location_embed("streamers présents sur place", streams["LAN"], finished=True, withlink=False),
                self.create_location_embed("participants à distance", streams["Online"], finished=True, withlink=False)
            ]
            
            # Edit the message
            if self.message:
                await self.message.edit(embeds=embeds, content="")
                await ctx.send("Embed final créé avec succès", ephemeral=True)
            else:
                await ctx.send("Erreur: Message non trouvé", ephemeral=True)
        except Exception as e:
            logger.error(f"Error in zevent_finish command: {e}")
            await ctx.send("Erreur lors de la création de l'embed final", ephemeral=True)
        
        
        
        