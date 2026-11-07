"""Zerator247Mixin — DM reminders when !request opens on ZeratoR 24/7.

A ZUnivers card asks players to ``!request`` a VOD on the ZeratoR 24/7 channel a
number of times. Requests are only accepted in a short window before each vote
(about ten minutes, near the end of the VOD airing), so members opt in with
``/zunivers 247 set`` and get a DM each time that window opens, reminding them
to type ``!request random`` (the 24/7 bot picks an archive VOD itself) or to
propose the VOD of their choice.
"""

from datetime import datetime

from interactions import IntervalTrigger, OptionType, SlashContext, Task, slash_option

from features.coloc.constants import PARIS_TZ
from features.links import shorten_url
from features.zerator247 import (
    CHOSEN_REQUEST_HINT,
    REQUEST_COMMAND,
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

from ._common import ZUNIVERS, GuildState, logger, zunivers_cmd

_REQUEST_GROUP = "247"
_REQUEST_GROUP_DESCRIPTION = "Rappels quand les !request s'ouvrent sur ZeratoR 24/7"


def _site_url(settings: dict) -> str | None:
    return settings.get("zerator247Url") or None


def _twitch_url(settings: dict) -> str | None:
    login = settings.get("zerator247TwitchLogin", "zerator_247")
    return f"https://twitch.tv/{login}" if login else None


class Zerator247Mixin:
    """Polls the 24/7 vote cycle and DMs subscribers when the requests window opens."""

    async def _load_request_subscriptions(self, guild_id: str) -> dict[str, RequestSubscription]:
        try:
            return await RequestReminderRepository(guild_id).load()
        except DatabaseError as e:
            logger.error("Could not load !request reminder subscriptions: %s", e)
            return {}

    @Task.create(IntervalTrigger(minutes=1))
    async def zerator247_checker(self):
        """Announce the requests window to each guild's subscribers once per cycle."""
        # Guilds following the same 24/7 site share one fetch of its cycle.
        cycles: dict[str, Cycle | None] = {}
        for state, settings in await self._guilds():
            site = _site_url(settings)
            if not state.request_subscriptions or not site:
                continue
            if site not in cycles:
                try:
                    cycles[site] = await Zerator247Client(site).get_cycle()
                except Zerator247Error as e:
                    logger.debug("ZeratoR 24/7 check skipped: %s", e)
                    cycles[site] = None
            cycle = cycles[site]
            if cycle is None or not should_notify(cycle, state.last_request_cycle_end):
                continue
            state.last_request_cycle_end = cycle.ends_at
            await self._announce_requests(state, settings, site, cycle)

    async def _announce_requests(
        self, state: GuildState, settings: dict, site: str, cycle: Cycle
    ) -> None:
        hour = datetime.now(PARIS_TZ).hour
        recipients = [s.user_id for s in state.request_subscriptions.values() if s.accepts(hour)]
        if not recipients:
            return
        votes_url = await shorten_url(f"{site.rstrip('/')}/#votes")
        message = build_request_reminder(
            cycle, twitch_url=_twitch_url(settings), votes_url=votes_url
        )
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
        if not _site_url(ZUNIVERS.get(ctx.guild_id)):
            await ctx.send("ZeratoR 24/7 n'est pas configuré sur ce serveur.", ephemeral=True)
            return
        if (debut is None) != (fin is None):
            await ctx.send(
                "Indique à la fois `debut` et `fin`, ou aucun des deux pour toute la journée.",
                ephemeral=True,
            )
            return
        subscription = RequestSubscription(str(ctx.author.id), debut, fin)
        state = await self._state(ctx.guild_id)
        try:
            await RequestReminderRepository(state.guild_id).save(subscription)
        except DatabaseError as e:
            logger.error("Could not save the !request reminder of %s: %s", ctx.author.id, e)
            await ctx.send("Impossible d'enregistrer le rappel pour le moment.", ephemeral=True)
            return
        state.request_subscriptions[subscription.user_id] = subscription
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
        state = await self._state(ctx.guild_id)
        try:
            removed = await RequestReminderRepository(state.guild_id).delete(user_id)
        except DatabaseError as e:
            logger.error("Could not remove the !request reminder of %s: %s", user_id, e)
            await ctx.send("Impossible de supprimer le rappel pour le moment.", ephemeral=True)
            return
        state.request_subscriptions.pop(user_id, None)
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
        site = _site_url(ZUNIVERS.get(ctx.guild_id))
        if not site:
            await ctx.send("ZeratoR 24/7 n'est pas configuré sur ce serveur.", ephemeral=True)
            return
        try:
            cycle = await Zerator247Client(site).get_cycle()
        except Zerator247Error as e:
            await ctx.send(f"⚠️ {e}", ephemeral=True)
            return
        state = await self._state(ctx.guild_id)
        subscription = state.request_subscriptions.get(str(ctx.author.id))
        mine = (
            f"Tu es abonné aux rappels, {subscription.describe_hours()}."
            if subscription
            else "Tu n'es pas abonné : `/zunivers 247 set` pour recevoir un MP."
        )
        await ctx.send(
            f"{describe_cycle(cycle)}\n"
            f"Commande à taper : `{REQUEST_COMMAND}` ({CHOSEN_REQUEST_HINT})\n{mine}",
            ephemeral=True,
        )
