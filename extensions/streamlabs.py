import asyncio
import os
from datetime import datetime
from typing import Optional
from urllib.parse import urlsplit

from aiohttp import ClientError
from babel.numbers import format_currency
from interactions import (
    Client,
    Embed,
    Extension,
    IntervalTrigger,
    Permissions,
    Task,
    listen,
    slash_command,
)
from twitchAPI.oauth import UserAuthenticationStorageHelper
from twitchAPI.twitch import Twitch
from twitchAPI.type import AuthScope

from src.core import logging as logutil
from src.core.config import ModuleConfig, global_config
from src.core.http import fetch, fetch_untrusted
from src.core.logging import log_failure
from src.core.tasks import spawn
from src.core.text import escape_md
from src.discord_ext.links import shorten_embeds
from src.discord_ext.messages import (
    GuildMessages,
    edit_message_if_changed,
    send_error,
)
from src.discord_ext.scopes import guild_scopes
from src.webui.schemas import (
    SchemaBase,
    enabled_field,
    register_module,
    ui,
)


@register_module("moduleStreamlabsCharity")
class StreamlabsCharityConfig(SchemaBase):
    __label__ = "Streamlabs Charity"
    __description__ = "Suivi d'une campagne Streamlabs Charity en direct."
    __icon__ = "❤️"
    __category__ = "Événements"

    enabled: bool = enabled_field()
    streamlabsChannelId: str = ui(
        "Salon",
        "channel",
        required=True,
        description="Salon pour le message de suivi (créé automatiquement).",
    )
    streamlabsPinMessage: bool = ui(
        "Épingler le message",
        "boolean",
        default=False,
        description="Épingler automatiquement le message de suivi.",
    )
    streamlabsTeamUrl: str = ui(
        "URL de la team",
        "url",
        required=True,
        description=(
            "URL publique de la team Streamlabs Charity suivie, "
            "ex. https://streamlabscharity.com/teams/@equipe/campagne."
        ),
    )


logger = logutil.init_logger(__name__)

# Constants
COLOR = 0x005EA5

STREAMLABS = ModuleConfig("moduleStreamlabsCharity")


def _team_url(settings: dict) -> str:
    """The guild's Streamlabs Charity team URL; empty means the tracker stays idle."""
    return str(settings.get("streamlabsTeamUrl") or "").strip()


def _api_root(team_url: str) -> str:
    """The Streamlabs Charity API on the team page's own site."""
    parts = urlsplit(team_url)
    return f"{parts.scheme}://{parts.netloc}/api/v1"


def campaign_api_url(team_url: str) -> str:
    """``…/teams/@team/campaign`` → the API URL of that campaign, on the same site."""
    return _api_root(team_url) + urlsplit(team_url).path


def members_api_url(team_url: str, campaign_id: object) -> str:
    """The API URL listing the members of the campaign the team URL points to."""
    return f"{_api_root(team_url)}/teams/{campaign_id}/members"


class StreamlabsCharityExtension(Extension):
    """Each enabled guild's Streamlabs Charity campaign, refreshed every minute.

    The settings are read at each refresh, so a dashboard save (another team,
    another channel) applies at the next one without a reload.
    """

    MODULE_KEYS = ("moduleStreamlabsCharity",)

    def __init__(self, bot: Client):
        self.bot: Client = bot
        self.twitch = None
        self.messages = GuildMessages(
            bot,
            slot="streamlabs",
            label="Suivi Streamlabs Charity",
            channel_key="streamlabsChannelId",
            pin_key="streamlabsPinMessage",
            initial_content="Initialisation Streamlabs Charity…",
            logger=logger,
        )

    @listen()
    async def on_startup(self):
        if not self.streamlabscharity.running:
            self.streamlabscharity.start()
        spawn(self.run(), name="streamlabs-twitch-auth", log=logger)

    async def async_drop(self):
        """Dashboard unload/reload: close the Twitch client the new instance will recreate."""
        if self.twitch is not None:
            try:
                await self.twitch.close()
            except Exception as e:
                logger.debug(f"Error closing Twitch client: {e}")
            self.twitch = None

    async def run(self):
        twitch_settings = global_config().get("twitch") or {}
        try:
            self.twitch = await Twitch(
                twitch_settings["twitchClientId"], twitch_settings["twitchClientSecret"]
            )
            helper = UserAuthenticationStorageHelper(
                twitch=self.twitch,
                scopes=[AuthScope.USER_READ_SUBSCRIPTIONS],
                storage_path="./data/twitchcreds.json",
            )
            await helper.bind()
        except Exception as e:
            log_failure(logger, f"Twitch init failed; live status disabled: {e}")
            self.twitch = None

    @Task.create(IntervalTrigger(minutes=1))
    async def streamlabscharity(self):
        guilds = STREAMLABS.items()
        self.messages.keep_only([guild_id for guild_id, _ in guilds])
        # A team followed by several guilds is fetched once per refresh.
        teams: dict[str, tuple[dict, dict]] = {}
        for guild_id, settings in guilds:
            url = _team_url(settings)
            if not url:
                logger.debug("Guild %s: Streamlabs team URL not configured; skipping", guild_id)
                continue
            try:
                message = await self.messages.get(guild_id, settings)
                if message is None:
                    logger.debug("Guild %s: Streamlabs message not configured yet", guild_id)
                    continue
                if url not in teams:
                    teams[url] = await self.fetch_team(url)
                campaign_data, members_dict = teams[url]
                await self.update_message(message, url, campaign_data, members_dict)
            except ClientError as e:
                logger.error("Guild %s: failed to fetch Streamlabs data: %s", guild_id, e)
            except KeyError as e:
                logger.error("Guild %s: unexpected Streamlabs data (%s)", guild_id, e)
            except Exception as e:  # noqa: BLE001 — one guild's failure must not stop the others
                log_failure(logger, "Guild %s: Streamlabs update failed: %s", guild_id, e)

    async def fetch_team(self, url: str) -> tuple[dict, dict]:
        """The team's campaign and its members, with their live status."""
        campaign_data = await self.fetch_campaign_data(url)
        members_dict = await self.fetch_members_data(url, campaign_data["id"])
        await self.update_live_status(members_dict)
        return campaign_data, members_dict

    async def fetch_campaign_data(self, url: str):
        # The team URL is set per guild from the dashboard: never fetch it, or
        # anything derived from it, as one of the bot's own trusted endpoints.
        return await fetch_untrusted(campaign_api_url(url), "json")

    async def fetch_members_data(self, url: str, campaign_id):
        members_data = await fetch_untrusted(members_api_url(url, campaign_id), "json")
        members = members_data["data"]
        while members_data.get("next_page_url"):
            # Pagination links come from the API response: treated as untrusted too.
            members_data = await fetch_untrusted(members_data["next_page_url"], "json")
            members.extend(members_data["data"])
        return {
            member["user"]["display_name"].lower(): self.create_member_data(member)
            for member in members
        }

    def create_member_data(self, member):
        return {
            "display_name": member["user"]["display_name"],
            "slug": member["user"]["display_name"].lower(),
            "is_live": False,
        }

    async def update_live_status(self, members_dict):
        if self.twitch is None:
            return
        CHUNK_SIZE = 100
        members_keys = list(members_dict.keys())
        for i in range(0, len(members_keys), CHUNK_SIZE):
            chunk_keys = members_keys[i : i + CHUNK_SIZE]
            async for stream in self.twitch.get_streams(user_login=chunk_keys):
                if stream.user_login in members_dict:
                    members_dict[stream.user_login]["is_live"] = True
                    members_dict[stream.user_login]["title"] = stream.title

    async def update_message(self, message, url, campaign_data, members_dict):
        members_dict = dict(sorted(members_dict.items()))
        online_members, offline_members = self.categorize_members(members_dict)
        members_str_online = self.split_members(online_members)
        members_str_offline = self.split_members(offline_members)
        embeds = await shorten_embeds(
            [
                self.create_campaign_embed(campaign_data, url),
                self.create_cause_embed(campaign_data),
                self.create_streamers_embed(members_str_online, members_str_offline, members_dict),
            ],
            tags=["streamlabs"],
        )
        await edit_message_if_changed(
            message, content="", embeds=embeds, ignore_timestamp=True, logger=logger
        )

    def categorize_members(self, members_dict):
        online_members = [
            f"[{escape_md(member['display_name'])}](https://twitch.tv/{member['slug']})"
            for member in members_dict.values()
            if member["is_live"]
        ]
        offline_members = [
            escape_md(member["display_name"])
            for member in members_dict.values()
            if not member["is_live"]
        ]
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

    def create_campaign_embed(self, campaign_data, url: str):
        formatted_amount = format_currency(
            campaign_data["amount_raised"] / 100, "EUR", locale="fr_FR"
        )
        return Embed(
            title=campaign_data["campaign"]["display_name"],
            description=(
                f"L'initiative **{campaign_data['campaign']['display_name']}** a récolté **{formatted_amount}** "
                f"pour **{campaign_data['campaign']['causable']['display_name']}**.\n\n"
                f"{campaign_data['campaign']['page_settings']['description']}"
            ),
            url=url or None,
            color=COLOR,
        )

    def create_cause_embed(self, campaign_data):
        social_links = {
            key: url
            for key, url in {
                "Discord": campaign_data["campaign"]["causable"]["page_settings"].get("discord"),
                "Facebook": campaign_data["campaign"]["causable"]["page_settings"].get("facebook"),
                "Instagram": campaign_data["campaign"]["causable"]["page_settings"].get(
                    "instagram"
                ),
                "Twitch": campaign_data["campaign"]["causable"]["page_settings"].get("twitch"),
                "Twitter": campaign_data["campaign"]["causable"]["page_settings"].get("twitter"),
                "Youtube": campaign_data["campaign"]["causable"]["page_settings"].get("youtube"),
                "URL": campaign_data["campaign"]["causable"]["page_settings"].get("misc_url"),
                "URL 2": campaign_data["campaign"]["causable"]["page_settings"].get("misc_url_2"),
                "URL 3": campaign_data["campaign"]["causable"]["page_settings"].get("misc_url_3"),
            }.items()
            if url
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
        self.add_embed_fields(
            embed_streamers, f"En ligne ({online_count}/{len(members_dict)})", members_str_online
        )
        self.add_embed_fields(
            embed_streamers,
            f"Hors ligne ({offline_count}/{len(members_dict)})",
            members_str_offline,
        )
        return embed_streamers

    def add_embed_fields(self, embed, title, members_str_list):
        embed.add_field(
            name=title,
            value=members_str_list[0] if members_str_list else "Aucun streamer",
            inline=False,
        )
        for members_str in members_str_list[1:]:
            embed.add_field(name="\u200b", value=members_str, inline=False)

    @slash_command(
        "endcharitycount",
        default_member_permissions=Permissions.ADMINISTRATOR,
        scopes=guild_scopes(STREAMLABS.guild_ids()),
    )
    async def endcharitycount(self, ctx):
        await ctx.send("Fin de la collecte de fond", ephemeral=True)
        settings = STREAMLABS.get(ctx.guild_id)
        url = _team_url(settings)
        if not url:
            await send_error(ctx, "URL de la team Streamlabs non configurée.")
            return
        message = await self.messages.get(ctx.guild_id, settings)
        if message is None:
            await send_error(ctx, "Message Streamlabs non configuré.")
            return
        campaign_data = await self.fetch_campaign_data(url)
        members_dict = await self.fetch_members_data(url, campaign_data["id"])
        members_list = self.list_members(members_dict)
        members_str = self.split_members(members_list)
        embeds = await shorten_embeds(
            [
                self.create_campaign_embed(campaign_data, url),
                self.create_cause_embed(campaign_data),
                self.create_final_members_embed(members_str),
            ],
            tags=["streamlabs"],
        )
        await edit_message_if_changed(
            message, content="", embeds=embeds, ignore_timestamp=True, logger=logger
        )

    def list_members(self, members_dict):
        return [escape_md(member["display_name"]) for member in members_dict.values()]

    def create_final_members_embed(self, members_str):
        embed_streamers = Embed(title="Participants", color=COLOR)
        self.add_embed_fields(embed_streamers, "Merci à tous <3", members_str)
        return embed_streamers
