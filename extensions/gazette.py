"""La Gazette du dimanche: the week of the server, posted once a week (P5, A1).

Every 15 minutes the task looks at the guilds where the Gazette is on: on the
configured day, from the configured hour (Paris), the week's edition is
claimed (``gazette_editions``, once per ISO week), gathered
(:mod:`src.discord_ext.gazette`), opened by a few lines of the AI where the
admins asked for them (:mod:`features.gazette.intro`), and posted in the
configured channel. A post
Discord refuses gives the claim back, so the next run tries again. A day the
bot was down is skipped: next week's edition covers its own week.

``/gazette`` shows what this week's edition says so far, to the member alone.
The dashboard has the same preview, and a "Publier maintenant" button.
"""

from __future__ import annotations

from typing import Any

from interactions import (
    Client,
    Extension,
    IntervalTrigger,
    SlashContext,
    Task,
    listen,
    slash_command,
)

from features.gazette import GazetteRepository, is_due, week_key
from features.gazette.edition import MAX_HOUR, MAX_WEEKDAY, MIN_HOUR, MIN_WEEKDAY
from features.gazette.intro import write_intro
from src.core import clock, log_context
from src.core import logging as logutil
from src.core.config import ModuleConfig
from src.core.errors import DatabaseError
from src.core.logging import log_failure
from src.discord_ext.channels import fetch_guild_channel
from src.discord_ext.gazette import gather_edition, gazette_embed, post_edition
from src.discord_ext.messages import guild_id_of, send_error
from src.discord_ext.scopes import guild_scopes
from src.webui.schemas import ModuleHelp, SchemaBase, enabled_field, register_module, ui

logger = logutil.init_logger(__name__)


@register_module("moduleGazette")
class GazetteConfig(SchemaBase):
    __label__ = "La Gazette"
    __description__ = (
        "Chaque semaine, un récapitulatif du serveur : les plus actifs, le message de la "
        "semaine, les nouveaux, les anniversaires, la playlist, les suggestions, les giveaways."
    )
    __icon__ = "📰"
    __category__ = "Communauté"
    __help__ = ModuleHelp(
        "Le résumé de la semaine du serveur, chaque semaine.",
        ("/gazette",),
        "Au jour et à l'heure choisis, la Gazette est publiée : les plus actifs, le message de la semaine, les nouveaux, les anniversaires à venir…",
    )

    enabled: bool = enabled_field()
    gazetteChannelId: str = ui(
        "Salon de la Gazette",
        "channel",
        required=True,
        description="Où publier la Gazette chaque semaine.",
    )
    gazetteWeekday: int = ui(
        "Jour de publication",
        "number",
        ge=MIN_WEEKDAY,
        le=MAX_WEEKDAY,
        default=7,
        description="1 : lundi … 7 : dimanche.",
    )
    gazetteHour: int = ui(
        "Heure de publication",
        "number",
        ge=MIN_HOUR,
        le=MAX_HOUR,
        default=18,
        description=(
            "Heure de Paris. Chaque rubrique n'apparaît que si son module est activé et qu'il "
            "s'est passé quelque chose : les plus actifs (XP), le message de la semaine "
            "(starboard), les anniversaires des 7 jours à venir, la playlist, les suggestions "
            "tranchées, les giveaways tirés et le feur de la semaine."
        ),
    )
    gazetteAiIntro: bool = ui(
        "Introduction écrite par l'IA",
        "boolean",
        default=False,
        description=(
            "Quelques phrases d'ouverture écrites par l'IA à partir du sommaire. Demande le "
            "module IA activé ; chaque Gazette compte un appel dans le quota quotidien de l'IA, "
            "et son texte (les pseudos qu'elle cite) est envoyé au fournisseur d'IA. L'aperçu "
            "et /gazette ne la montrent pas."
        ),
    )


GAZETTE = ModuleConfig("moduleGazette")


def _bounded(value: Any, low: int, high: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(number, high))


class GazetteExtension(Extension):
    # The Gazette reads the other modules' news, only where they are on.
    MODULE_KEYS = (
        "moduleGazette",
        "moduleXp",
        "moduleStarboard",
        "moduleBirthday",
        "moduleSpotify",
        "moduleSuggestions",
        "moduleGiveaway",
        "moduleFeur",
    )
    HELP_LABEL = "📰 La Gazette"

    def __init__(self, bot: Client):
        self.bot = bot

    def repository(self, guild_id: str) -> GazetteRepository:
        return GazetteRepository(guild_id)

    @listen()
    async def on_startup(self) -> None:
        if not self.publish_due.running:
            self.publish_due.start()

    @Task.create(IntervalTrigger(minutes=15))
    async def publish_due(self) -> None:
        now = clock.now()
        for guild_id, settings in GAZETTE.items():
            weekday = _bounded(settings.get("gazetteWeekday"), MIN_WEEKDAY, MAX_WEEKDAY, 7)
            hour = _bounded(settings.get("gazetteHour"), MIN_HOUR, MAX_HOUR, 18)
            if not is_due(now, weekday, hour):
                continue
            with log_context.scope(guild_id=guild_id):
                try:
                    await self.publish_week(guild_id, settings, now)
                except Exception as e:
                    log_failure(logger, "Gazette of guild %s not published: %s", guild_id, e)

    async def publish_week(self, guild_id: str, settings: dict, now: Any) -> bool:
        """Post this week's edition in *guild_id* unless it was; ``True`` when this run did."""
        key = week_key(now)
        repo = self.repository(guild_id)
        if not await repo.claim_edition(key, now):
            return False
        channel = await fetch_guild_channel(
            self.bot, guild_id, str(settings.get("gazetteChannelId") or ""), logger=logger
        )
        if channel is None or not hasattr(channel, "send"):
            logger.warning("Gazette of guild %s: its channel is unusable", guild_id)
            await repo.release_edition(key)
            return False
        try:
            edition = await gather_edition(self.bot, guild_id, now)
            edition.ai_intro = await self._ai_intro(guild_id, edition)
            message = await post_edition(channel, edition)
        except Exception:
            await repo.release_edition(key)
            raise
        await repo.set_edition_message(key, str(channel.id), str(message.id))
        logger.info("Gazette %s published in guild %s", key, guild_id)
        return True

    async def _ai_intro(self, guild_id: str, edition: Any) -> str:
        """The AI's opening lines where they were asked for; ``""`` rather than no paper."""
        try:
            return await write_intro(guild_id, edition)
        except Exception as e:
            log_failure(logger, "Gazette intro of guild %s failed: %s", guild_id, e)
            return ""

    @slash_command(
        name="gazette",
        description="Ce que dit la Gazette de cette semaine, pour l'instant",
        scopes=guild_scopes(GAZETTE.guild_ids()),
    )
    async def gazette(self, ctx: SlashContext) -> None:
        guild_id = guild_id_of(ctx)
        await ctx.defer(ephemeral=True)
        try:
            edition = await gather_edition(self.bot, guild_id, clock.now())
        except DatabaseError as e:
            logger.warning("Gazette preview of guild %s failed: %s", guild_id, e)
            await send_error(ctx, "Je n'arrive pas à lire les nouvelles pour l'instant : réessaie.")
            return
        await ctx.send(embeds=[gazette_embed(edition)], ephemeral=True)


def setup(bot: Client) -> None:
    GazetteExtension(bot)


__all__ = ["GAZETTE", "GazetteConfig", "GazetteExtension", "setup"]
