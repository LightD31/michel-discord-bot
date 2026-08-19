import asyncio
import os
from datetime import datetime

import pytz
from interactions import (
    BaseChannel,
    Client,
    Embed,
    Extension,
    IntervalTrigger,
    Message,
    Task,
    TimestampStyles,
    listen,
    utils,
)

from src.core import logging as logutil
from src.core.config import load_config
from src.core.http import fetch
from src.discord_ext.messages import edit_message_if_changed, fetch_or_create_persistent_message
from src.discord_ext.paginator import CustomPaginator
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
        required=True,
        description="URL de base de l'API Speedons (inclut le slug de la campagne).",
    )
    speedonsIconUrl: str | None = ui(
        "URL de l'icône",
        "url",
        description="Icône affichée dans les embeds. Vide = aucune icône.",
    )


logger = logutil.init_logger(os.path.basename(__file__))

_, _module_config, _enabled_servers = load_config("moduleSpeedons")
_cfg = _module_config.get(_enabled_servers[0], {}) if _enabled_servers else {}

# Both come from the guild config — the code carries no Speedons URL.
BASE_URL = _cfg.get("speedonsApiUrl", "")
ICON_URL = _cfg.get("speedonsIconUrl") or None
COLOR = 0xDBEA2B


def get_url(endpoint):
    return f"{BASE_URL}/{endpoint}"


async def get_data(url):
    try:
        payload = await fetch(url, return_type="json", retries=2)
        return payload["data"]
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
        self.paginator: CustomPaginator | None = None

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

        events_data, attendees_data, amount_data, incentives_data = await asyncio.gather(
            get_data(events_url),
            get_data(attendees_url),
            get_data(amount_url),
            get_data(incentives_url),
        )
        if None in (events_data, attendees_data, amount_data, incentives_data):
            logger.warning("Speedons API data incomplete; skipping this update")
            return
        embeds = []
        embedlive = None
        current_run = None
        # Fetch amount
        amount = float(amount_data["amount"])
        embed = Embed(
            title=f"Speedons 4 ({amount:.2f}€)",
            timestamp=datetime.now(pytz.UTC),
            color=0xDBEA2B,
            thumbnail=ICON_URL,
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
                    title=f"Run en cours ({amount:.2f}€)",
                    timestamp=datetime.now(pytz.UTC),
                    color=0xDBEA2B,
                    thumbnail=ICON_URL,
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
                    title=f"Speedons 4 ({amount:.2f}€)",
                    timestamp=datetime.now(pytz.UTC),
                    color=0xDBEA2B,
                )
            i = i + 1
        embeds.append(embed)
        if self.paginator is None:
            self.paginator = CustomPaginator.create_from_embeds(self.bot, *embeds, timeout=3600)
        # Same landing page as before this paginator was reused: the live run if
        # there is one, otherwise back to the first page.
        self.paginator.set_pages(
            embeds, page_index=int(current_run / 5) if current_run is not None else 0
        )
        paginator_dict = self.paginator.to_dict()
        await edit_message_if_changed(
            self.message,
            embeds=paginator_dict["embeds"],
            components=paginator_dict["components"],
            ignore_timestamp=True,
            logger=logger,
        )

        await edit_message_if_changed(
            self.message2,
            content="",
            embed=embedlive
            or Embed(
                title=f"Pas de run en cours ({amount:.2f}€)",
                timestamp=datetime.now(pytz.UTC),
                color=0xDBEA2B,
                thumbnail=ICON_URL,
            ),
            ignore_timestamp=True,
            logger=logger,
        )
