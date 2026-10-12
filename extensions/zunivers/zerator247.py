"""Zerator247Mixin — DM reminders when !request opens on ZeratoR 24/7.

A ZUnivers card asks players to ``!request`` a VOD on the ZeratoR 24/7 channel a
number of times. Requests are only accepted in a short window before each vote
(about ten minutes, near the end of the VOD airing), so members opt in with
``/zunivers request set`` and get a DM each time that window opens.
"""

from datetime import datetime

from interactions import IntervalTrigger, OptionType, SlashContext, Task, slash_option

from features.coloc.constants import PARIS_TZ
from features.links import shorten_url
from features.zerator247 import (
    Cycle,
    RequestReminderRepository,
    RequestSubscription,
    Zerator247Client,
    Zerator247Error,
    build_request_reminder,
    describe_cycle,
    should_notify,
)
from src.core.errors import DatabaseError
from src.discord_ext.messages import fetch_user_safe

from ._common import enabled_servers, logger, module_config, zunivers_cmd

_REQUEST_GROUP = "request"
_REQUEST_GROUP_DESCRIPTION = "Rappels quand les !request s'ouvrent sur ZeratoR 24/7"


def _site_url() -> str | None:
    return module_config.get("zerator247Url") or None


def _twitch_url() -> str | None:
    login = module_config.get("zerator247TwitchLogin", "zerator_247")
    return f"https://twitch.tv/{login}" if login else None


class Zerator247Mixin:
    """Polls the 24/7 vote cycle and DMs subscribers when the requests window opens."""

    request_subscriptions: dict[str, RequestSubscription]
    # End time of the last cycle announced, so one requests window is announced once.
    _last_request_cycle_end: datetime | None

    def _request_repository(self) -> RequestReminderRepository:
        return RequestReminderRepository(enabled_servers[0])

    async def _load_request_subscriptions(self) -> None:
        try:
            self.request_subscriptions = await self._request_repository().load()
        except DatabaseError as e:
            logger.error("Could not load !request reminder subscriptions: %s", e)
            self.request_subscriptions = {}

    async def _fetch_cycle(self) -> Cycle | None:
        site = _site_url()
        if not site:
            return None
        return await Zerator247Client(site).get_cycle()

    @Task.create(IntervalTrigger(minutes=1))
    async def zerator247_checker(self):
        """Announce the requests window to subscribers once per cycle."""
        if not self.request_subscriptions or not _site_url():
            return
        try:
            cycle = await self._fetch_cycle()
        except Zerator247Error as e:
            logger.debug("ZeratoR 24/7 check skipped: %s", e)
            return
        if cycle is None or not should_notify(cycle, self._last_request_cycle_end):
            return
        self._last_request_cycle_end = cycle.ends_at

        hour = datetime.now(PARIS_TZ).hour
        recipients = [s.user_id for s in self.request_subscriptions.values() if s.accepts(hour)]
        if not recipients:
            return
        site = _site_url()
        votes_url = await shorten_url(f"{site.rstrip('/')}/#votes") if site else None
        message = build_request_reminder(cycle, twitch_url=_twitch_url(), votes_url=votes_url)
        sent = 0
        for user_id in recipients:
            _, user = await fetch_user_safe(self.bot, user_id)
            if not user:
                logger.warning("Could not fetch user %s for the !request reminder", user_id)
                continue
            try:
                await user.send(message)
                sent += 1
            except Exception as e:
                logger.warning("Could not DM the !request reminder to %s: %s", user_id, e)
        logger.info("Sent the ZeratoR 24/7 !request reminder to %d member(s)", sent)

    @zunivers_cmd.subcommand(
        group_name=_REQUEST_GROUP,
        group_description=_REQUEST_GROUP_DESCRIPTION,
        sub_cmd_name="set",
        sub_cmd_description="Reçois un MP quand les !request s'ouvrent sur ZeratoR 24/7",
    )
    @slash_option(
        name="debut",
        description="Heure à partir de laquelle recevoir les rappels (par défaut : toute la journée)",
        opt_type=OptionType.INTEGER,
        required=False,
        min_value=0,
        max_value=23,
    )
    @slash_option(
        name="fin",
        description="Heure à partir de laquelle ne plus recevoir les rappels",
        opt_type=OptionType.INTEGER,
        required=False,
        min_value=0,
        max_value=23,
    )
    async def request_set(
        self, ctx: SlashContext, debut: int | None = None, fin: int | None = None
    ):
        if not _site_url():
            await ctx.send("ZeratoR 24/7 n'est pas configuré sur ce serveur.", ephemeral=True)
            return
        if (debut is None) != (fin is None):
            await ctx.send(
                "Indique à la fois `debut` et `fin`, ou aucun des deux pour toute la journée.",
                ephemeral=True,
            )
            return
        subscription = RequestSubscription(str(ctx.author.id), debut, fin)
        try:
            await self._request_repository().save(subscription)
        except DatabaseError as e:
            logger.error("Could not save the !request reminder of %s: %s", ctx.author.id, e)
            await ctx.send("Impossible d'enregistrer le rappel pour le moment.", ephemeral=True)
            return
        self.request_subscriptions[subscription.user_id] = subscription
        await ctx.send(
            "Tu recevras un MP quand les !request s'ouvrent sur ZeratoR 24/7, "
            f"{subscription.describe_hours()} (heure de Paris).",
            ephemeral=True,
        )
        logger.info(
            "!request reminder set for %s (%s)",
            ctx.author.display_name,
            subscription.describe_hours(),
        )

    @zunivers_cmd.subcommand(
        group_name=_REQUEST_GROUP,
        group_description=_REQUEST_GROUP_DESCRIPTION,
        sub_cmd_name="remove",
        sub_cmd_description="Arrête les rappels !request ZeratoR 24/7",
    )
    async def request_remove(self, ctx: SlashContext):
        user_id = str(ctx.author.id)
        try:
            removed = await self._request_repository().delete(user_id)
        except DatabaseError as e:
            logger.error("Could not remove the !request reminder of %s: %s", user_id, e)
            await ctx.send("Impossible de supprimer le rappel pour le moment.", ephemeral=True)
            return
        self.request_subscriptions.pop(user_id, None)
        await ctx.send(
            "Rappels !request supprimés." if removed else "Tu n'avais pas de rappel !request.",
            ephemeral=True,
        )

    @zunivers_cmd.subcommand(
        group_name=_REQUEST_GROUP,
        group_description=_REQUEST_GROUP_DESCRIPTION,
        sub_cmd_name="status",
        sub_cmd_description="Indique quand s'ouvrent les prochaines !request sur ZeratoR 24/7",
    )
    async def request_status(self, ctx: SlashContext):
        if not _site_url():
            await ctx.send("ZeratoR 24/7 n'est pas configuré sur ce serveur.", ephemeral=True)
            return
        try:
            cycle = await self._fetch_cycle()
        except Zerator247Error as e:
            await ctx.send(f"⚠️ {e}", ephemeral=True)
            return
        subscription = self.request_subscriptions.get(str(ctx.author.id))
        mine = (
            f"Tu es abonné aux rappels, {subscription.describe_hours()}."
            if subscription
            else "Tu n'es pas abonné : `/zunivers request set` pour recevoir un MP."
        )
        await ctx.send(f"{describe_cycle(cycle)}\n{mine}", ephemeral=True)
