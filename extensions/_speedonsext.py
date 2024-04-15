import os
from datetime import datetime, timedelta
from typing import Optional

import pytz
import requests
from interactions import (
    BaseChannel,
    Client,
    ComponentContext,
    Embed,
    Extension,
    IntervalTrigger,
    Message,
    Task,
    TimestampStyles,
    listen,
    utils,
)
from interactions.ext import paginators

from src import logutil

logger = logutil.init_logger(os.path.basename(__file__))

BASE_URL = "https://tracker.speedons.fr/api/campaigns/cn6o21t7m4hq41ukqtag"
ICON_URL = "https://speedons.fr/static/b476f2d8ad4a19d2393eb4cff9486cc9/c6b81/icon.png"
COLOR = 0xDBEA2B


def get_url(endpoint):
    return f"{BASE_URL}/{endpoint}"


def get_data(url):
    try:
        response = requests.get(url, timeout=5)
        return response.json()["data"]
    except Exception as e:
        logger.error("Error fetching data from %s: %s", url, e)
        return None


class SpeedonsExtension(Extension):
    def __init__(self, bot: Client):
        self.bot = bot
        self.planning_channel_id = int(os.getenv("TWITCH_PLANNING_CHANNEL_ID"))
        self.channel: Optional[BaseChannel] = None
        self.message: Optional[Message] = None
        self.message2: Optional[Message] = None

    @listen()
    async def on_startup(self):
        self.channel: BaseChannel = await self.bot.fetch_channel(
            self.planning_channel_id
        )
        self.message: Message = await self.channel.fetch_message(1212843311300345896)
        self.message2: Message = await self.channel.fetch_message(1213553914176348271)
        self.get_speedons_schedule.start()
        await self.get_speedons_schedule()

    @Task.create(IntervalTrigger(minutes=5))
    async def get_speedons_schedule(self):
        events_url = get_url("events")
        attendees_url = get_url("attendees")
        amount_url = BASE_URL
        incentives_url = get_url("challenges")

        events_data = get_data(events_url)
        attendees_data = get_data(attendees_url)
        amount_data = get_data(amount_url)
        incentives_data = get_data(incentives_url)
        embeds = []
        embedlive = None
        current_run = None
        # Fetch amount
        amount = float(amount_data["amount"])
        embed = Embed(
            title=f"Speedons 4 ({amount:.2f}€) (Actualisation {utils.timestamp_converter(datetime.now(pytz.utc)+timedelta(minutes=5)).format(TimestampStyles.RelativeTime)})",
            timestamp=datetime.now(pytz.UTC),
            color=0xDBEA2B,
            thumbnail="https://speedons.fr/static/b476f2d8ad4a19d2393eb4cff9486cc9/c6b81/icon.png",
        )
        # Create an incentive dict
        incentives = {}
        for incentive in incentives_data:
            if incentive.get("event_id"):
                incentives[incentive["event_id"]] = incentive["name"]
        # Create an attendees dict
        attendees = {}
        for attendee in attendees_data:
            attendees[attendee["id"]] = attendee["name"]
        i = 0
        # Parsing JSON
        for event in events_data:
            runner = ""
            commentator = ""
            category = ""
            estimate = ""
            incentive = ""
            # list attendee
            runner = ", ".join(
                [
                    attendees.get(runner_id["attendee_id"], "Unknown")
                    for runner_id in event["participants"]
                    if runner_id["role"] == "ACTOR"
                ]
            )

            commentator = ", ".join(
                [
                    attendees.get(commentator_id["attendee_id"], "Unknown")
                    for commentator_id in event["participants"]
                    if commentator_id["role"] == "COMMENTATOR"
                ]
            )
            if runner != "":
                if "," in runner:
                    runner = f"\nRunners : **{runner}**"
                else:
                    runner = f"\nRunner : **{runner}**"
            if commentator != "":
                if "," in commentator:
                    commentator = f"\nCommentateurs : **{commentator}**"
                else:
                    commentator = f"\nCommentateur : **{commentator}**"
            if event["properties"].get("category", ""):
                category = (
                    f"\nCatégorie : **{event['properties'].get('category', '')}**"
                )
            if event["properties"].get("estimate", ""):
                if category:
                    estimate = (
                        f" - Estimé : **{event['properties'].get('estimate', '')}**"
                    )
                else:
                    estimate = (
                        f"\nEstimé : **{event['properties'].get('estimate', '')}**"
                    )
            if event["id"] in incentives:
                incentive = f"\nIncentive : **{incentives[event['id']]}**"

            if (
                utils.timestamp_converter(event["start_date"])
                < datetime.now(pytz.UTC)
                < utils.timestamp_converter(event["end_date"])
            ):
                current_run = i
                embedlive = Embed(
                    title=f"Run en cours ({amount:.2f}€) (Actualisation {utils.timestamp_converter(datetime.now(pytz.utc)+timedelta(minutes=5)).format(TimestampStyles.RelativeTime)})",
                    timestamp=datetime.now(pytz.UTC),
                    color=0xDBEA2B,
                    thumbnail="https://speedons.fr/static/b476f2d8ad4a19d2393eb4cff9486cc9/c6b81/icon.png",
                )
                embedlive.add_field(
                    name=event["properties"].get(
                        "game", event["properties"].get("subject", "Unknown")
                    ),
                    value=f"Début: **{utils.timestamp_converter(event['start_date']).format(TimestampStyles.ShortTime)}** Fin: **{utils.timestamp_converter(event['end_date']).format(TimestampStyles.ShortTime)}**{category}{estimate}{runner}{commentator}{incentive}",
                )
                embed.add_field(
                    name=f"<:zrtON:962320783038890054> {event['properties'].get('game', event['properties'].get('subject', 'Unknown'))} <:zrtON:962320783038890054>",
                    value=f"Début: **{utils.timestamp_converter(event['start_date']).format(TimestampStyles.ShortTime)}** Fin: **{utils.timestamp_converter(event['end_date']).format(TimestampStyles.ShortTime)}**{category}{estimate}{runner}{commentator}{incentive}",
                )
            else:
                embed.add_field(
                    name=event["properties"].get(
                        "game", event["properties"].get("subject", "Unknown")
                    ),
                    value=f"Début: **{utils.timestamp_converter(event['start_date']).format(TimestampStyles.ShortTime)}** Fin: **{utils.timestamp_converter(event['end_date']).format(TimestampStyles.ShortTime)}**{category}{estimate}{runner}{commentator}{incentive}",
                )
            if len(embed.fields) == 5:
                embeds.append(embed)
                embed = Embed(
                    title=f"Speedons 4 ({amount:.2f}€) (Actualisation {utils.timestamp_converter(datetime.now(pytz.utc)+timedelta(minutes=5)).format(TimestampStyles.RelativeTime)})",
                    timestamp=datetime.now(pytz.UTC),
                    color=0xDBEA2B,
                )
            i = i + 1
        embeds.append(embed)
        paginator = CustomPaginator.create_from_embeds(self.bot, *embeds, timeout=3600)
        if current_run is not None:
            paginator.page_index = int(current_run / 5)
        await self.message.edit(
            embeds=paginator.to_dict()["embeds"],
            components=paginator.to_dict()["components"],
        )

        await self.message2.edit(
            content="",
            embed=embedlive
            or Embed(
                title=f"Pas de run en cours ({amount:.2f}€)",
                timestamp=datetime.now(pytz.UTC),
                color=0xDBEA2B,
                thumbnail="https://speedons.fr/static/b476f2d8ad4a19d2393eb4cff9486cc9/c6b81/icon.png",
            ),
        )


class CustomPaginator(paginators.Paginator):
    # Override the functions here
    async def _on_button(
        self, ctx: ComponentContext, *args, **kwargs
    ) -> Optional[Message]:
        if self._timeout_task:
            self._timeout_task.ping.set()
        match ctx.custom_id.split("|")[1]:
            case "first":
                self.page_index = 0
            case "last":
                self.page_index = len(self.pages) - 1
            case "next":
                if (self.page_index + 1) < len(self.pages):
                    self.page_index += 1
            case "back":
                if self.page_index >= 1:
                    self.page_index -= 1
            case "select":
                self.page_index = int(ctx.values[0])
            case "callback":
                if self.callback:
                    return await self.callback(ctx)

        await ctx.edit_origin(**self.to_dict())
        return None
