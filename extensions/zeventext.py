import os
from interactions import (
    Extension, Client, listen, Message, BaseChannel,
    Task, IntervalTrigger, Embed, File, TimestampStyles, utils
)
from src import logutil
from src.utils import fetch, load_config
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
from twitchAPI.twitch import Twitch
import asyncio
from dataclasses import dataclass

logger = logutil.init_logger(os.path.basename(__file__))
config, _, _ = load_config()

@dataclass
class StreamerInfo:
    display_name: str
    twitch_name: str
    is_online: bool
    location: str

def split_streamer_list(streamer_list: str, max_length: int = 1024, withlink = False) -> List[str]:
    chunks = []
    current_chunk = []
    current_length = 0
    if not withlink:
        max_length = max_length // 6
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
    MESSAGE_ID = 1279187668185645177
    API_URL = "https://zevent.fr/api/"
    PLANNING_API_URL = "https://api.zevent.gdoc.fr/events/upcoming"
    STREAMLABS_API_URL = "https://streamlabscharity.com/api/v1/teams/@zevent-2024/zevent-2024"
    UPDATE_INTERVAL = 30  # 1 minute
    MILESTONE_INTERVAL = 100000  # 100k

    def __init__(self, client: Client):
        self.client: Client = client
        self.channel: Optional[BaseChannel] = None
        self.message: Optional[Message] = None
        self.twitch: Optional[Twitch] = None
        self.last_milestone = 0

    @listen()
    async def on_startup(self):
        self.channel = await self.client.fetch_channel(self.CHANNEL_ID)
        self.message = await self.channel.fetch_message(self.MESSAGE_ID)
        self.twitch = await Twitch(config["twitch"]["twitchClientId"], config["twitch"]["twitchClientSecret"])
        self.zevent.start()
        await self.zevent()

    @Task.create(IntervalTrigger(seconds=UPDATE_INTERVAL))
    async def zevent(self):
        try:
            data, planning_data, streamlabs_data = await asyncio.gather(
                fetch(self.API_URL, return_type="json"),
                fetch(self.PLANNING_API_URL, return_type="json"),
                fetch(self.STREAMLABS_API_URL, return_type="json")
            )

            if data is None or planning_data is None or streamlabs_data is None:
                logger.error("Failed to fetch data from API")
                return

            total_amount, total_int = self.get_total_amount(data, streamlabs_data)
            nombre_viewers = data["viewersCount"]["formatted"]
            streams = await self.categorize_streams(data["live"])

            embeds = [
                self.create_main_embed(total_amount, nombre_viewers),
                self.create_location_embed("streamers présents sur place", streams["LAN"]),
                self.create_location_embed("participants à distance", streams["Online"], withlink=False),
                self.create_planning_embed(planning_data["data"])
            ]

            file = File("data/Zevent_logo.png")
            await self.message.edit(embeds=embeds, content="", files=[file])

            await self.check_and_send_milestone(total_int)

        except Exception as e:
            logger.error(f"Failed to update message: {e}")
            await self.send_simplified_update(total_amount)

    def get_total_amount(self, data: Dict, streamlabs_data: Dict) -> tuple[str, float]:
        total_amount = data["donationAmount"]["formatted"]
        total_int = data["donationAmount"]["number"]
        total_from_streamlabs = streamlabs_data["amount_raised"] / 100  # Streamlabs API returns amount in cents
        logger.debug(f"Total from Zevent: {total_int}, Total from Streamlabs: {total_from_streamlabs}")
        if total_from_streamlabs > total_int:
            total_int = total_from_streamlabs
            total_amount = f"{total_int:,.2f} €".replace(",", " ")
        return total_amount, total_int

    async def check_and_send_milestone(self, total_amount: float):
        current_milestone = int(total_amount // self.MILESTONE_INTERVAL * self.MILESTONE_INTERVAL)

        if current_milestone > self.last_milestone:
            # Doesn't send message for the first milestone to avoid spam when bot starts
            if current_milestone > 100000:
                milestone_message = f"🎉 Nouveau palier atteint : {current_milestone:,} € récoltés ! 🎉".replace(",", " ")
                await self.channel.send(milestone_message)
            self.last_milestone = current_milestone

    async def send_simplified_update(self, total_amount: str):
        try:
            simple_embed = Embed(
                title="Zevent Update",
                description=f"Total récolté: {total_amount}\n\nDésolé, nous rencontrons des difficultés techniques pour afficher les détails des streamers.",
                color=0x59af37,
                timestamp=datetime.now()
            )
            await self.message.edit(embeds=[simple_embed], content="")
        except Exception as e:
            logger.error(f"Failed to send simplified update: {e}")

    async def categorize_streams(self, streams: List[Dict]) -> Dict[str, Dict[str, StreamerInfo]]:
        categorized = {"LAN": {}, "Online": {}}
        
        twitch_usernames = list(set(stream["twitch"] for stream in streams))
        
        batch_size = 100
        live_streamers = set()
        
        for i in range(0, len(twitch_usernames), batch_size):
            batch = twitch_usernames[i:i+batch_size]
            async for stream in self.twitch.get_streams(user_login=batch):
                live_streamers.add(stream.user_login.lower())

        for stream in streams:
            location = stream["location"]
            twitch_name = stream["twitch"].lower()
            display_name = stream["display"].replace("_", "\\_")
            is_online = twitch_name in live_streamers
            
            streamer_info = StreamerInfo(display_name, twitch_name, is_online, location)
            categorized[location][display_name] = streamer_info
        
        return categorized

    def create_main_embed(self, total_amount: str, nombre_viewers: str) -> Embed:
        return Embed(
            title="Zevent 2024",
            description=f"Total récolté: {total_amount}\nViewers cumulés: {nombre_viewers}",
            color=0x59af37,
            timestamp=datetime.now(),
            thumbnail="attachment://Zevent_logo.png",
            footer=f"Source: zevent.fr ❤️"
        )

    def create_location_embed(self, title: str, streams: Dict[str, StreamerInfo], withlink = True) -> Embed:
        streamer_count = len(streams)
        embed = Embed(title=f"Les {streamer_count} {title}", color=0x59af37, footer="Source: zevent.fr / Twitch ❤️", timestamp=datetime.now())
        
        online_streamers = [s for s in streams.values() if s.is_online]
        offline_streamers = [s for s in streams.values() if not s.is_online]

        for status, streamers in [("En ligne", online_streamers), ("Hors-ligne", offline_streamers)]:
            if not streamers:
                continue

            streamer_list = ', '.join(
                f"[{s.display_name}](https://www.twitch.tv/{s.twitch_name})" if withlink else s.display_name
                for s in streamers
            )

            chunks = split_streamer_list(streamer_list, max_length=1024, withlink=withlink)
            for i, chunk in enumerate(chunks, 1):
                field_name = status if len(chunks) == 1 else f"{status} {i}/{len(chunks)}"
                embed.add_field(name=field_name, value=chunk or "Aucun streamer", inline=True)

        if len(embed.fields) == 0:
            embed.add_field(name="Status", value="Aucun streamer en ce moment", inline=False)

        return embed

    def create_planning_embed(self, events: List[Dict]) -> Embed:
        embed = Embed(title="Prochains évènements", color=0x59af37, footer="Source: zevent.gdoc.fr ❤️", timestamp=datetime.now())
        
        sorted_events = sorted(events, key=lambda x: x['start_at'])
        
        for event in sorted_events:
            start_time = datetime.fromisoformat(event['start_at'].replace('Z', '+00:00')).replace(tzinfo=timezone.utc)
            end_time = datetime.fromisoformat(event['finished_at'].replace('Z', '+00:00')).replace(tzinfo=timezone.utc)
            
            field_name = event['name']
            
            duration = end_time - start_time
            
            time_str = (f"{str(utils.timestamp_converter(start_time)).format(TimestampStyles.LongDateTime)} - "
                        f"{str(utils.timestamp_converter(end_time)).format(TimestampStyles.ShortTime)}"
                        if duration >= timedelta(minutes=20)
                        else f"{str(utils.timestamp_converter(start_time)).format(TimestampStyles.LongDateTime)}")
            
            field_value = f"{time_str}\n"
            
            if event['description']:
                field_value += f"{event['description']}\n"
            
            if event['hosts']:
                hosts = ', '.join([host['name'].replace("_", "\\_") for host in event['hosts']])
                field_value += f"Hosts: {hosts}\n"
            
            if event['participants']:
                participants = ', '.join([participant['name'].replace("_", "\\_") for participant in event['participants']])
                field_value += f"Participants: {participants}"
            
            embed.add_field(name=field_name, value=field_value, inline=True)
        
        return embed