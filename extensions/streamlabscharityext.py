from interactions import Embed, Extension, listen, Task, IntervalTrigger, Client, slash_command
from babel.numbers import format_currency
from datetime import datetime
import asyncio
from dotenv import load_dotenv
from aiohttp import ClientError
from src import logutil
import os
from src.utils import fetch, escape_md
from twitchAPI.oauth import UserAuthenticationStorageHelper
from twitchAPI.twitch import Twitch
from twitchAPI.type import AuthScope

logger = logutil.init_logger(os.path.basename(__file__))
load_dotenv()

# Constants
STREAMLABS_URL = "https://streamlabscharity.com/teams/@streamers-4-palestinians/streamers-4-palestinians"
COLOR = 0x005EA5
CHANNEL_ID = 1246046086041440287
MESSAGE_ID = 1246172482537263136

class StreamlabsCharityExtension(Extension):
    def __init__(self, bot: Client):
        self.bot: Client = bot
        self.twitch = None
        self.client_id = os.getenv("TWITCH_CLIENT_ID")
        self.client_secret = os.getenv("TWITCH_CLIENT_SECRET")

    @listen()
    async def on_startup(self):
        self.streamlabscharity.start()
        asyncio.create_task(self.run())

    async def run(self):
        self.twitch = await Twitch(self.client_id, self.client_secret)
        helper = UserAuthenticationStorageHelper(
            twitch=self.twitch,
            scopes=[AuthScope.USER_READ_SUBSCRIPTIONS],
            storage_path="./data/twitchcreds.json",
        )
        await helper.bind()

    @Task.create(IntervalTrigger(minutes=1))
    async def streamlabscharity(self):
        try:
            channel = await self.bot.fetch_channel(CHANNEL_ID)
            message = await channel.fetch_message(MESSAGE_ID)
            campaign_data = await self.fetch_campaign_data()
            members_dict = await self.fetch_members_data(campaign_data["id"])
            await self.update_live_status(members_dict)
            await self.update_message(message, campaign_data, members_dict)
        except ClientError as e:
            logger.error(f"Failed to fetch channel or message: {e}")
        except KeyError as e:
            logger.error(f"Key error while processing JSON data: {e}")
        except Exception as e:
            logger.error(f"An unexpected error occurred: {e}")

    async def fetch_campaign_data(self):
        return await fetch(STREAMLABS_URL.replace("teams", "api/v1/teams"), "json")

    async def fetch_members_data(self, campaign_id):
        members_data = await fetch(f"https://streamlabscharity.com/api/v1/teams/{campaign_id}/members", "json")
        members = members_data["data"]
        while members_data.get("next_page_url"):
            members_data = await fetch(members_data["next_page_url"], "json")
            members.extend(members_data["data"])
        return {member["user"]["display_name"].lower(): self.create_member_data(member) for member in members}

    def create_member_data(self, member):
        return {
            "display_name": member["user"]["display_name"],
            "slug": member["user"]["display_name"].lower(),
            "is_live": False,
        }

    async def update_live_status(self, members_dict):
        CHUNK_SIZE = 100
        members_keys = list(members_dict.keys())
        for i in range(0, len(members_keys), CHUNK_SIZE):
            chunk_keys = members_keys[i:i+CHUNK_SIZE]
            async for stream in self.twitch.get_streams(user_login=chunk_keys):
                if stream.user_login in members_dict:
                    members_dict[stream.user_login]["is_live"] = True
                    members_dict[stream.user_login]["title"] = stream.title

    async def update_message(self, message, campaign_data, members_dict):
        members_dict = dict(sorted(members_dict.items()))
        online_members, offline_members = self.categorize_members(members_dict)
        members_str_online = self.split_members(online_members)
        members_str_offline = self.split_members(offline_members)
        embeds = [
            self.create_campaign_embed(campaign_data),
            self.create_cause_embed(campaign_data),
            self.create_streamers_embed(members_str_online, members_str_offline, members_dict)
        ]
        await message.edit(content="", embeds=embeds)

    def categorize_members(self, members_dict):
        # online_members = [
        #     f"[{escape_md(member['display_name'])}](https://twitch.tv/{member['slug']} '{member.get('title', '')}')"
        #     for member in members_dict.values()
        #     if member["is_live"]
        # ]
        online_members = [
            f"[{escape_md(member['display_name'])}](https://twitch.tv/{member['slug']})"
            for member in members_dict.values()
            if member["is_live"]
        ]
        offline_members = [escape_md(member["display_name"]) for member in members_dict.values() if not member["is_live"]]
        return online_members, offline_members

    def split_members(self, members):
        members_str_list = []
        current_str = ""
        for member in members:
            if len(current_str) + len(member) + 2 > 1024:
                members_str_list.append(current_str)
                current_str = member
            else:
                current_str = member if not current_str else f"{current_str}, {member}"
        if current_str:
            members_str_list.append(current_str)
        return members_str_list

    def create_campaign_embed(self, campaign_data):
        formatted_amount = format_currency(campaign_data["amount_raised"] / 100, "EUR", locale="fr_FR")
        return Embed(
            title=campaign_data["campaign"]["display_name"],
            description=(
                f"L'initiative **{campaign_data['campaign']['display_name']}** a récolté **{formatted_amount}** "
                f"pour **{campaign_data['campaign']['causable']['display_name']}**.\n\n"
                f"{campaign_data['campaign']['page_settings']['description']}"
            ),
            url=STREAMLABS_URL,
            color=COLOR,
        )

    def create_cause_embed(self, campaign_data):
        social_links = {
            key: url for key, url in {
                "Discord": campaign_data["campaign"]["causable"]["page_settings"].get("discord"),
                "Facebook": campaign_data["campaign"]["causable"]["page_settings"].get("facebook"),
                "Instagram": campaign_data["campaign"]["causable"]["page_settings"].get("instagram"),
                "Twitch": campaign_data["campaign"]["causable"]["page_settings"].get("twitch"),
                "Twitter": campaign_data["campaign"]["causable"]["page_settings"].get("twitter"),
                "Youtube": campaign_data["campaign"]["causable"]["page_settings"].get("youtube"),
                "URL": campaign_data["campaign"]["causable"]["page_settings"].get("misc_url"),
                "URL 2": campaign_data["campaign"]["causable"]["page_settings"].get("misc_url_2"),
                "URL 3": campaign_data["campaign"]["causable"]["page_settings"].get("misc_url_3"),
            }.items() if url
        }
        social_links_str = ", ".join(f"[{key}]({url})" for key, url in social_links.items())
        return Embed(
            title=campaign_data["campaign"]["causable"]["display_name"],
            description=f"{campaign_data['campaign']['causable']['description']}\n\n{social_links_str}",
            thumbnail=campaign_data["campaign"]["causable"]["avatar"]["url"],
            url=campaign_data["campaign"]["causable"]["page_settings"]["website_url"],
            color=COLOR,
        )

    def create_streamers_embed(self, members_str_online, members_str_offline, members_dict):
        online_count = sum(member["is_live"] for member in members_dict.values())
        offline_count = len(members_dict) - online_count
        embed_streamers = Embed(
            title="Participants",
            color=COLOR,
            timestamp=datetime.now(),
        )
        self.add_embed_fields(embed_streamers, f"En ligne ({online_count}/{len(members_dict)})", members_str_online)
        self.add_embed_fields(embed_streamers, f"Hors ligne ({offline_count}/{len(members_dict)})", members_str_offline)
        return embed_streamers

    def add_embed_fields(self, embed, title, members_str_list):
        embed.add_field(
            name=title,
            value=members_str_list[0] if members_str_list else "Aucun streamer",
            inline=False,
        )
        for members_str in members_str_list[1:]:
            embed.add_field(name="\u200b", value=members_str, inline=False)

    @slash_command("endcharitycount")
    async def endcharitycount(self, ctx):
        await ctx.send("Fin de la collecte de fond", ephemeral=True)
        channel = await self.bot.fetch_channel(CHANNEL_ID)
        message = await channel.fetch_message(MESSAGE_ID)
        campaign_data = await self.fetch_campaign_data()
        members_dict = await self.fetch_members_data(campaign_data["id"])
        members_list = self.list_members(members_dict)
        members_str = self.split_members(members_list)
        embeds = [
            self.create_campaign_embed(campaign_data),
            self.create_cause_embed(campaign_data),
            self.create_final_members_embed(members_str)
        ]
        await message.edit(content="", embeds=embeds)

    def list_members(self, members_dict):
        return [escape_md(member["display_name"]) for member in members_dict.values()]

    def create_final_members_embed(self, members_str):
        embed_streamers = Embed(title="Participants", color=COLOR)
        self.add_embed_fields(embed_streamers, "Merci à tous <3", members_str)
        return embed_streamers
