import asyncio
from datetime import datetime

import pytz
from interactions import (
    Client,
    Embed,
    Extension,
    IntervalTrigger,
    Task,
    TimestampStyles,
    listen,
    utils,
)

from src.core import logging as logutil
from src.core.config import ModuleConfig
from src.core.http import fetch_untrusted
from src.core.logging import log_failure
from src.core.urls import redact_url
from src.discord_ext.messages import GuildMessages, edit_message_if_changed
from src.discord_ext.paginator import CustomPaginator
from src.webui.schemas import (
    SchemaBase,
    enabled_field,
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


logger = logutil.init_logger(__name__)

SPEEDONS = ModuleConfig("moduleSpeedons")
COLOR = 0xDBEA2B


async def get_data(url):
    # The API URL is a guild setting: only public addresses, and no retries.
    try:
        payload = await fetch_untrusted(url, return_type="json")
        return payload["data"]
    except Exception as e:
        log_failure(logger, "Error fetching data from %s: %s", redact_url(url), e)
        return None


async def render_schedule(base_url: str, icon_url: str | None):
    """The schedule pages, the live-run embed and the page to land on, or None.

    The live embed says so when no run is in progress.

    *base_url* is the campaign's API URL (``speedonsApiUrl``, the slug
    included): the code carries no Speedons URL.
    """
    events_url = f"{base_url}/events"
    attendees_url = f"{base_url}/attendees"
    amount_url = base_url
    incentives_url = f"{base_url}/challenges"

    events_data, attendees_data, amount_data, incentives_data = await asyncio.gather(
        get_data(events_url),
        get_data(attendees_url),
        get_data(amount_url),
        get_data(incentives_url),
    )
    if None in (events_data, attendees_data, amount_data, incentives_data):
        logger.warning("Speedons API data incomplete; skipping this update")
        return None
    embeds = []
    embedlive = None
    current_run = None
    # Fetch amount
    amount = float(amount_data["amount"])
    embed = Embed(
        title=f"Speedons 4 ({amount:.2f}€)",
        timestamp=datetime.now(pytz.UTC),
        color=0xDBEA2B,
        thumbnail=icon_url,
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
                thumbnail=icon_url,
            )
            embedlive.add_field(
                name=event["properties"].get("game", event["properties"].get("subject", "Unknown")),
                value=f"Début: **{utils.timestamp_converter(event['start_date']).format(TimestampStyles.ShortTime)}** Fin: **{utils.timestamp_converter(event['end_date']).format(TimestampStyles.ShortTime)}**{category}{estimate}{runner}{commentator}{incentive}",
            )
            embed.add_field(
                name=f"<:zrtON:962320783038890054> {event['properties'].get('game', event['properties'].get('subject', 'Unknown'))} <:zrtON:962320783038890054>",
                value=f"Début: **{utils.timestamp_converter(event['start_date']).format(TimestampStyles.ShortTime)}** Fin: **{utils.timestamp_converter(event['end_date']).format(TimestampStyles.ShortTime)}**{category}{estimate}{runner}{commentator}{incentive}",
            )
        else:
            embed.add_field(
                name=event["properties"].get("game", event["properties"].get("subject", "Unknown")),
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
    live = embedlive or Embed(
        title=f"Pas de run en cours ({amount:.2f}€)",
        timestamp=datetime.now(pytz.UTC),
        color=COLOR,
        thumbnail=icon_url,
    )
    return embeds, live, (int(current_run / 5) if current_run is not None else 0)


class SpeedonsExtension(Extension):
    """Each enabled guild's Speedons schedule and live run, refreshed every 5 minutes.

    The settings are read at each refresh, so a dashboard save applies at the
    next one without a reload.
    """

    MODULE_KEYS = ("moduleSpeedons",)

    def __init__(self, bot: Client):
        self.bot = bot
        self.schedule_messages = GuildMessages(
            bot,
            slot="speedons:schedule",
            label="Planning Speedons",
            channel_key="speedonsChannelId",
            pin_key="speedonsPinMessages",
            initial_content="Initialisation du planning Speedons…",
            logger=logger,
        )
        self.live_messages = GuildMessages(
            bot,
            slot="speedons:live",
            label="Run Speedons en cours",
            channel_key="speedonsChannelId",
            pin_key="speedonsPinMessages",
            initial_content="Initialisation du run en cours…",
            logger=logger,
        )
        # One paginator per guild's schedule message, reused across refreshes.
        self.paginators: dict[str, CustomPaginator] = {}

    @listen()
    async def on_startup(self):
        if not self.get_speedons_schedule.running:
            self.get_speedons_schedule.start()
        await self.get_speedons_schedule()

    @Task.create(IntervalTrigger(minutes=5))
    async def get_speedons_schedule(self):
        guilds = SPEEDONS.items()
        enabled = [guild_id for guild_id, _ in guilds]
        self.schedule_messages.keep_only(enabled)
        self.live_messages.keep_only(enabled)
        for guild_id in [g for g in self.paginators if g not in enabled]:
            del self.paginators[guild_id]
        # Guilds following the same campaign share one rendering per refresh.
        rendered: dict[tuple[str, str | None], tuple | None] = {}
        for guild_id, settings in guilds:
            base_url = str(settings.get("speedonsApiUrl") or "").rstrip("/")
            if not base_url:
                logger.debug("Guild %s: Speedons API URL not configured; skipping", guild_id)
                continue
            icon_url = settings.get("speedonsIconUrl") or None
            try:
                key = (base_url, icon_url)
                if key not in rendered:
                    rendered[key] = await render_schedule(base_url, icon_url)
                if rendered[key] is not None:
                    await self._publish(guild_id, settings, *rendered[key])
            except Exception as e:  # noqa: BLE001 — one guild's failure must not stop the others
                log_failure(logger, "Guild %s: Speedons update failed: %s", guild_id, e)

    async def _publish(self, guild_id, settings, embeds, live_embed, page_index):
        schedule = await self.schedule_messages.get(guild_id, settings)
        live = await self.live_messages.get(guild_id, settings)
        if schedule is None or live is None:
            logger.debug("Guild %s: Speedons messages not available yet; skipping", guild_id)
            return
        paginator = self.paginators.get(guild_id)
        if paginator is None:
            paginator = CustomPaginator.create_from_embeds(self.bot, *embeds, timeout=3600)
            self.paginators[guild_id] = paginator
        # Same landing page as before this paginator was reused: the live run if
        # there is one, otherwise back to the first page.
        paginator.set_pages(embeds, page_index=page_index)
        paginator_dict = paginator.to_dict()
        await edit_message_if_changed(
            schedule,
            embeds=paginator_dict["embeds"],
            components=paginator_dict["components"],
            ignore_timestamp=True,
            logger=logger,
        )
        await edit_message_if_changed(
            live, content="", embed=live_embed, ignore_timestamp=True, logger=logger
        )
