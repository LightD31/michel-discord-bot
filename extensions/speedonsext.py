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
from src.config_manager import load_config
from src.helpers import fetch_or_create_persistent_message
from src.webui.schemas import (
    SchemaBase,
    enabled_field,
    hidden_message_id,
    register_module,
    ui,
)


@register_module("moduleSpeedons")
class SpeedonsConfig(SchemaBase):
    __label__ = "Speedons"
    __description__ = "Planning et suivi en temps réel de l'événement Speedons."
    __icon__ = "🏃"
    __category__ = "Événements"

    enabled: bool = enabled_field()
    speedonsChannelId: str = ui(
        "Salon",
        "channel",
        required=True,
        description="Salon contenant les messages du planning (créés automatiquement).",
    )
    speedonsPinMessages: bool = ui(
        "Épingler les messages",
        "boolean",
        default=False,
        description="Épingler automatiquement les messages planning et live.",
    )
    speedonsScheduleMessageId: str | None = hidden_message_id(
        "Message planning", "speedonsChannelId"
    )
    speedonsLiveMessageId: str | None = hidden_message_id(
        "Message run en cours", "speedonsChannelId"
    )
    speedonsApiUrl: str = ui(
        "URL API",
        "url",
        description="URL de base de l'API Speedons (inclut le slug de la campagne).",
        default="https://tracker.speedons.fr/api/campaigns?slug=2025",
    )
    speedonsIconUrl: str = ui(
        "URL de l'icône",
        "url",
        description="URL de l'icône affichée dans les embeds.",
        default="https://speedons.fr/static/b476f2d8ad4a19d2393eb4cff9486cc9/c6b81/icon.png",
    )


logger = logutil.init_logger(os.path.basename(__file__))

_, _module_config, _enabled_servers = load_config("moduleSpeedons")
_cfg = _module_config.get(_enabled_servers[0], {}) if _enabled_servers else {}

BASE_URL = _cfg.get("speedonsApiUrl", "https://tracker.speedons.fr/api/campaigns?slug=2025")
ICON_URL = _cfg.get(
    "speedonsIconUrl", "https://speedons.fr/static/b476f2d8ad4a19d2393eb4cff9486cc9/c6b81/icon.png"
)
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
        _raw_channel = _cfg.get("speedonsChannelId") or os.getenv("TWITCH_PLANNING_CHANNEL_ID", "0")
        self.planning_channel_id = int(_raw_channel) if _raw_channel else 0
        self.schedule_message_id = _cfg.get("speedonsScheduleMessageId")
        self.live_message_id = _cfg.get("speedonsLiveMessageId")
        self.pin_messages = bool(_cfg.get("speedonsPinMessages", False))
        self.guild_id: str | None = _enabled_servers[0] if _enabled_servers else None
        self.channel: BaseChannel | None = None
        self.message: Message | None = None
        self.message2: Message | None = None

    async def _ensure_messages(self) -> bool:
        if self.message is None:
            self.message = await fetch_or_create_persistent_message(
                self.bot,
                channel_id=self.planning_channel_id,
                message_id=self.schedule_message_id,
                module_name="moduleSpeedons",
                message_id_key="speedonsScheduleMessageId",
                guild_id=self.guild_id,
                initial_content="Initialisation du planning Speedons…",
                pin=self.pin_messages,
                logger=logger,
            )
        if self.message2 is None:
            self.message2 = await fetch_or_create_persistent_message(
                self.bot,
                channel_id=self.planning_channel_id,
                message_id=self.live_message_id,
                module_name="moduleSpeedons",
                message_id_key="speedonsLiveMessageId",
                guild_id=self.guild_id,
                initial_content="Initialisation du run en cours…",
                pin=self.pin_messages,
                logger=logger,
            )
        return self.message is not None and self.message2 is not None

    @listen()
    async def on_startup(self):
        if self.planning_channel_id:
            try:
                self.channel = await self.bot.fetch_channel(self.planning_channel_id)
            except Exception as e:
                logger.error("Could not fetch Speedons channel: %s", e)
        await self._ensure_messages()
        self.get_speedons_schedule.start()
        await self.get_speedons_schedule()

    @Task.create(IntervalTrigger(minutes=5))
    async def get_speedons_schedule(self):
        if not await self._ensure_messages():
            logger.debug("Speedons messages not available yet; skipping update")
            return
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
            title=f"Speedons 4 ({amount:.2f}€) (Actualisation {utils.timestamp_converter(datetime.now(pytz.utc) + timedelta(minutes=5)).format(TimestampStyles.RelativeTime)})",
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
                category = f"\nCatégorie : **{event['properties'].get('category', '')}**"
            if event["properties"].get("estimate", ""):
                if category:
                    estimate = f" - Estimé : **{event['properties'].get('estimate', '')}**"
                else:
                    estimate = f"\nEstimé : **{event['properties'].get('estimate', '')}**"
            if event["id"] in incentives:
                incentive = f"\nIncentive : **{incentives[event['id']]}**"

            if (
                utils.timestamp_converter(event["start_date"])
                < datetime.now(pytz.UTC)
                < utils.timestamp_converter(event["end_date"])
            ):
                current_run = i
                embedlive = Embed(
                    title=f"Run en cours ({amount:.2f}€) (Actualisation {utils.timestamp_converter(datetime.now(pytz.utc) + timedelta(minutes=5)).format(TimestampStyles.RelativeTime)})",
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
                    title=f"Speedons 4 ({amount:.2f}€) (Actualisation {utils.timestamp_converter(datetime.now(pytz.utc) + timedelta(minutes=5)).format(TimestampStyles.RelativeTime)})",
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
    async def _on_button(self, ctx: ComponentContext, *args, **kwargs) -> Message | None:
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
