"""Giveaway Discord extension — ``/giveaway`` slash command + draw scheduler.

Slash commands:
- ``/giveaway start prize:<str> duration:<30m|2h|1d> winners:<int> [allow_host_win:<bool>]`` — post a
  giveaway embed with its "Participer" / "Se retirer" buttons.
- ``/giveaway end giveaway:<id>`` — end a giveaway early and draw winners now.
- ``/giveaway reroll giveaway:<id> [winners:<int>]`` — pick fresh winners
  excluding any prior winners.
- ``/giveaway list`` — list active giveaways on this guild.
- ``/giveaway cancel giveaway:<id>`` — cancel without drawing.

The ``giveaway`` option autocompletes: the server's open giveaways for end and
cancel, its recently drawn ones for reroll (the value is the announcement's
message id, which can still be pasted).

Button entry: each click adds or removes the member in the giveaway's
``entrants`` with one atomic update, and the announcement shows the exact
count a few seconds later. A background task scans every 30 s for due
giveaways and draws winners among the stored entrants.
Persistence: per-guild ``giveaways`` collection. Enabled per-guild via
``moduleGiveaway``.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Literal

import emoji as emoji_lib
from interactions import (
    ActionRow,
    AllowedMentions,
    AutocompleteContext,
    BaseChannel,
    Button,
    ButtonStyle,
    Client,
    ComponentContext,
    Embed,
    Extension,
    IntervalTrigger,
    Message,
    OptionType,
    Permissions,
    SlashContext,
    Task,
    component_callback,
    listen,
    slash_command,
    slash_default_member_permission,
    slash_option,
)

from features.giveaway import MAX_WINNERS, Giveaway, GiveawayRepository, pick_winners
from features.polls import parse_duration  # reused: same DSL as /poll
from src.core import clock
from src.core.debounce import Coalescer
from src.core.logging import log_failure
from src.core.text import EMBED_DESCRIPTION_LIMIT, clip
from src.discord_ext.autocomplete import MAX_CHOICES, matching_choices
from src.discord_ext.embeds import Colors, format_discord_timestamp
from src.discord_ext.messages import (
    edit_message_if_changed,
    require_guild,
    send_error,
    send_success,
)
from src.discord_ext.scopes import guild_scopes

from ._common import (
    GIVEAWAY,
    guild_allow_host_win,
    guild_emoji,
    logger,
)

# Floor on giveaway duration. Anything shorter than 10 s is almost certainly a
# typo and races the background scheduler.
MIN_DURATION_SECONDS = 10
# Hard ceiling — 30 days.
MAX_DURATION_SECONDS = 30 * 86400
# Affiche les mentions tant que la liste reste lisible dans un champ d'embed.
MAX_LISTED_PARTICIPANTS = 15
MAX_PARTICIPANTS_FIELD_CHARS = 900
# A draw Discord doesn't answer for (5xx, timeout) is retried at the next runs
# (every 30 s), then the giveaway closes without winners.
MAX_DRAW_FAILURES = 10


def _winners_only(winners: list[str]) -> AllowedMentions:
    """Ping the winners and nothing else: the prize is whatever the host typed."""
    return AllowedMentions(users=[int(w) for w in winners])


# The title is "🏁 GIVEAWAY TERMINÉ — {prize}" and Discord caps titles at 256.
MAX_PRIZE_LENGTH = 200
# Clicks on a giveaway within this many seconds share one re-render of its participants.
REFRESH_DELAY_SECONDS = 3
MAX_DESCRIPTION_LENGTH = 1000
# The buttons' ids are the same on every giveaway: the message they sit on says which one.
ENTER_ID = "giveaway:enter"
LEAVE_ID = "giveaway:leave"
_CUSTOM_EMOJI = re.compile(r"^<a?:\w{2,32}:\d{17,20}>$")


def _button_emoji(raw: str) -> str | None:
    """The configured emoji when Discord takes it on a button, else None (no emoji).

    A message whose button carries anything else is refused by Discord, which
    would stop the giveaway from being posted at all.
    """
    if _CUSTOM_EMOJI.match(raw) or emoji_lib.is_emoji(raw):
        return raw
    return None


def _entry_buttons(giveaway: Giveaway) -> list[ActionRow]:
    """The "Participer" / "Se retirer" row of an open giveaway."""
    return [
        ActionRow(
            Button(
                style=ButtonStyle.SUCCESS,
                label="Participer",
                emoji=_button_emoji(giveaway.emoji),
                custom_id=ENTER_ID,
            ),
            Button(style=ButtonStyle.SECONDARY, label="Se retirer", custom_id=LEAVE_ID),
        )
    ]


def _participants_field_value(entrant_ids: list[str]) -> str:
    """Format the participants field as mentions, or fallback to a count."""
    count = len(entrant_ids)
    if count == 0:
        return "Aucun"

    mentions = ", ".join(f"<@{uid}>" for uid in entrant_ids)
    if count > MAX_LISTED_PARTICIPANTS or len(mentions) > MAX_PARTICIPANTS_FIELD_CHARS:
        suffix = "participant·e" if count == 1 else "participant·e·s"
        return f"{count} {suffix}"
    return mentions


def _build_embed(
    giveaway: Giveaway,
    *,
    host_name: str,
    host_avatar: str | None,
    closed: bool = False,
    cancelled: bool = False,
    winners_mention: str | None = None,
    entrants: list[str] | None = None,
    entry_count: int | None = None,
) -> Embed:
    """Render the giveaway embed for any of its three lifecycle states.

    Metadata (closing time, winner count, host, participants) lives in proper
    embed fields so Discord renders consistent label/value pairs on both
    desktop and mobile — packing them into ``description`` produced misaligned
    labels with apparently-empty values on narrow viewports.
    """
    title_prefix = "🎁 GIVEAWAY"
    color = Colors.UTIL
    if cancelled:
        title_prefix = "🚫 GIVEAWAY ANNULÉ"
        color = Colors.WARNING
    elif closed:
        title_prefix = "🏁 GIVEAWAY TERMINÉ"
        color = Colors.SUCCESS

    embed = Embed(title=f"{title_prefix} — {giveaway.prize}", color=color)

    # Description: prose only — the giveaway's own description plus the
    # call-to-action / status line.
    description_lines: list[str] = []
    if giveaway.description:
        description_lines.append(giveaway.description)
    if cancelled:
        description_lines.append("*Ce giveaway a été annulé.*")
    elif closed:
        if winners_mention:
            description_lines.append(f"🎉 **Gagnant·e·s :** {winners_mention}")
        else:
            description_lines.append("**Aucune participation valide.**")
    else:
        description_lines.append("Clique sur **Participer** pour tenter ta chance !")
    if description_lines:
        embed.description = "\n\n".join(description_lines)

    # Fields: structured metadata. Inline so the row stays compact.
    if not cancelled and not closed:
        embed.add_field(
            name="Fermeture",
            value=(
                f"{format_discord_timestamp(giveaway.ends_at, 'R')}\n"
                f"{format_discord_timestamp(giveaway.ends_at, 'F')}"
            ),
            inline=True,
        )
    embed.add_field(
        name="Gagnant·e·s à tirer",
        value=str(giveaway.winners_count),
        inline=True,
    )
    embed.add_field(name="Hôte", value=f"<@{giveaway.host_id}>", inline=True)
    participants_value: str | None = None
    if entrants is not None:
        participants_value = _participants_field_value(entrants)
    elif entry_count is not None:
        participants_value = str(entry_count)
    if participants_value is not None:
        embed.add_field(name="Participants", value=participants_value, inline=True)

    embed.set_footer(text=f"Lancé par {host_name}", icon_url=host_avatar)
    return embed


def _footer_host(message: Message | None, giveaway: Giveaway) -> tuple[str, str | None]:
    """The host name and avatar the announcement's footer shows, to keep them on a re-render."""
    host_name = f"ID:{giveaway.host_id}"
    host_avatar: str | None = None
    if message is None or not message.embeds:
        return host_name, host_avatar
    footer = getattr(message.embeds[0], "footer", None)
    footer_text = getattr(footer, "text", None)
    if isinstance(footer_text, str) and footer_text.startswith("Lancé par "):
        resolved = footer_text.removeprefix("Lancé par ").strip()
        if resolved:
            host_name = resolved
    footer_icon = getattr(footer, "icon_url", None)
    if isinstance(footer_icon, str) and footer_icon:
        host_avatar = footer_icon
    return host_name, host_avatar


DrawOutcome = Literal["drawn", "taken", "retry"]


def _allow_host(giveaway: Giveaway) -> bool:
    """Whether the host may win: the giveaway's own setting, else the server's."""
    if giveaway.allow_host_win is not None:
        return giveaway.allow_host_win
    return guild_allow_host_win(giveaway.guild_id)


def _eligible(giveaway: Giveaway) -> list[str]:
    """The entrants who may win: the host only where the giveaway allows it."""
    if _allow_host(giveaway):
        return list(giveaway.entrants)
    return [uid for uid in giveaway.entrants if uid != giveaway.host_id]


class GiveawayExtension(Extension):
    MODULE_KEYS = ("moduleGiveaway",)

    def __init__(self, bot: Client):
        self.bot: Client = bot
        # A burst of clicks on a giveaway re-renders its announcement once.
        self._refreshes = Coalescer(REFRESH_DELAY_SECONDS, name="giveaway-refresh", log=logger)

    def _repo(self, guild_id: str | int) -> GiveawayRepository:
        return GiveawayRepository(str(guild_id))

    @listen()
    async def on_startup(self) -> None:
        for gid in GIVEAWAY.guild_ids():
            try:
                await self._repo(gid).ensure_indexes()
            except Exception as e:
                log_failure(logger, "Could not init giveaway indexes for %s: %s", gid, e)
        if not self.check_giveaways.running:
            self.check_giveaways.start()
        logger.info("Giveaway extension ready (%d guild(s))", len(GIVEAWAY.guild_ids()))

    # ------------------------------------------------------------------
    # Slash commands
    # ------------------------------------------------------------------

    @slash_command(
        name="giveaway",
        description="Gérer les giveaways",
        sub_cmd_name="start",
        sub_cmd_description="Lancer un giveaway avec bouton de participation",
        scopes=guild_scopes(GIVEAWAY.guild_ids()),
    )
    @slash_option(
        "prize",
        "Lot mis en jeu",
        opt_type=OptionType.STRING,
        required=True,
        max_length=MAX_PRIZE_LENGTH,
        argument_name="prize",
    )
    @slash_option(
        "duree",
        "Durée du giveaway (ex: 30m, 2h, 1d)",
        opt_type=OptionType.STRING,
        required=True,
        argument_name="duration",
    )
    @slash_option(
        "gagnants",
        "Nombre de gagnant·e·s à tirer",
        opt_type=OptionType.INTEGER,
        required=False,
        min_value=1,
        max_value=MAX_WINNERS,
        argument_name="winners",
    )
    @slash_option(
        "description",
        "Texte additionnel affiché dans l'embed",
        opt_type=OptionType.STRING,
        required=False,
        max_length=MAX_DESCRIPTION_LENGTH,
        argument_name="description",
    )
    @slash_option(
        "organisateur_peut_gagner",
        "Autoriser l'organisateur de ce giveaway à gagner",
        opt_type=OptionType.BOOLEAN,
        required=False,
        argument_name="allow_host_win",
    )
    @slash_default_member_permission(Permissions.MANAGE_GUILD)
    async def giveaway_start(
        self,
        ctx: SlashContext,
        prize: str,
        duration: str,
        winners: int = 1,
        description: str | None = None,
        allow_host_win: bool | None = None,
    ) -> None:
        if not await require_guild(ctx):
            return
        seconds = parse_duration(duration)
        if seconds is None:
            await send_error(ctx, "Format de durée invalide. Utilisez par ex. `30m`, `2h`, `1d`.")
            return
        if seconds < MIN_DURATION_SECONDS:
            await send_error(ctx, f"Durée minimale : {MIN_DURATION_SECONDS} s.")
            return
        if seconds > MAX_DURATION_SECONDS:
            await send_error(ctx, "Durée maximale : 30 jours.")
            return

        ends_at = clock.now() + timedelta(seconds=seconds)
        effective_allow_host_win = (
            allow_host_win if allow_host_win is not None else guild_allow_host_win(ctx.guild_id)
        )

        giveaway = Giveaway(
            guild_id=str(ctx.guild_id),
            channel_id=str(ctx.channel.id),
            message_id="0",  # placeholder until we have the real message id
            host_id=str(ctx.user.id),
            prize=prize,
            description=description,
            emoji=guild_emoji(ctx.guild_id),
            allow_host_win=effective_allow_host_win,
            winners_count=winners,
            ends_at=ends_at,
        )

        embed = _build_embed(
            giveaway,
            host_name=ctx.user.username,
            host_avatar=str(ctx.user.avatar_url) if ctx.user.avatar_url else None,
            entrants=[],
        )
        message = await ctx.send(embeds=[embed], components=_entry_buttons(giveaway))
        giveaway.message_id = str(message.id)

        try:
            giveaway.id = await self._repo(ctx.guild_id).add(giveaway)
        except Exception as e:
            log_failure(logger, "Could not persist giveaway: %s", e)
            # Its buttons would answer nothing: take the announcement down.
            try:
                await message.delete()
            except Exception as delete_error:
                logger.warning("Could not delete unsaved giveaway %s: %s", message.id, delete_error)
            await send_error(ctx, "Le giveaway n'a pas pu être enregistré, réessaie plus tard.")
            return

        logger.info(
            "Giveaway %s started by %s (prize=%r, winners=%d, host_can_win=%s, ends=%s)",
            giveaway.id,
            ctx.user.username,
            prize,
            winners,
            effective_allow_host_win,
            ends_at.isoformat(),
        )

    @giveaway_start.subcommand(
        sub_cmd_name="end",
        sub_cmd_description="Tirer immédiatement les gagnants d'un giveaway en cours",
    )
    @slash_option(
        "giveaway",
        "Giveaway en cours (choisis-le dans la liste)",
        opt_type=OptionType.STRING,
        required=True,
        autocomplete=True,
        argument_name="message_id",
    )
    @slash_default_member_permission(Permissions.MANAGE_GUILD)
    async def giveaway_end(self, ctx: SlashContext, message_id: str) -> None:
        if not await require_guild(ctx):
            return
        await ctx.defer(ephemeral=True)
        giveaway = await self._repo(ctx.guild_id).get_by_message(message_id)
        if not giveaway:
            await send_error(ctx, "Aucun giveaway trouvé pour ce message.")
            return
        if giveaway.drawn:
            await send_error(ctx, "Ce giveaway est déjà clos.")
            return
        if giveaway.cancelled:
            await send_error(ctx, "Ce giveaway a été annulé.")
            return
        outcome = await self._draw(giveaway)
        if outcome == "drawn":
            await send_success(ctx, "Tirage effectué.")
        elif outcome == "taken":
            await send_error(ctx, "Ce giveaway vient d'être clos.")
        else:
            await send_error(ctx, "Discord n'a pas répondu, réessaie dans un instant.")

    @giveaway_start.subcommand(
        sub_cmd_name="cancel",
        sub_cmd_description="Annuler un giveaway en cours sans tirage",
    )
    @slash_option(
        "giveaway",
        "Giveaway en cours (choisis-le dans la liste)",
        opt_type=OptionType.STRING,
        required=True,
        autocomplete=True,
        argument_name="message_id",
    )
    @slash_default_member_permission(Permissions.MANAGE_GUILD)
    async def giveaway_cancel(self, ctx: SlashContext, message_id: str) -> None:
        if not await require_guild(ctx):
            return
        await ctx.defer(ephemeral=True)
        giveaway = await self._repo(ctx.guild_id).get_by_message(message_id)
        if not giveaway or giveaway.id is None:
            await send_error(ctx, "Aucun giveaway trouvé pour ce message.")
            return
        if giveaway.drawn:
            await send_error(ctx, "Ce giveaway est déjà clos.")
            return
        if giveaway.cancelled:
            await send_error(ctx, "Ce giveaway a déjà été annulé.")
            return
        if not await self._repo(ctx.guild_id).mark_cancelled(giveaway.id):
            await send_error(ctx, "Ce giveaway vient d'être clos.")
            return
        giveaway.cancelled = True
        try:
            message = await self._fetch_message(giveaway)
            if message is not None:
                host_name, host_avatar = _footer_host(message, giveaway)
                embed = _build_embed(
                    giveaway, host_name=host_name, host_avatar=host_avatar, cancelled=True
                )
                await edit_message_if_changed(message, embeds=[embed], components=[], logger=logger)
        except Exception as e:
            logger.warning("Could not edit cancelled giveaway message: %s", e)
        await send_success(ctx, "Giveaway annulé.")

    @giveaway_start.subcommand(
        sub_cmd_name="reroll",
        sub_cmd_description="Tirer de nouveaux gagnants pour un giveaway clos",
    )
    @slash_option(
        "giveaway",
        "Giveaway clos (choisis-le dans la liste)",
        opt_type=OptionType.STRING,
        required=True,
        autocomplete=True,
        argument_name="message_id",
    )
    @slash_option(
        "gagnants",
        "Nombre de nouveaux gagnants (par défaut : même qu'à l'origine)",
        opt_type=OptionType.INTEGER,
        required=False,
        min_value=1,
        max_value=MAX_WINNERS,
        argument_name="winners",
    )
    @slash_default_member_permission(Permissions.MANAGE_GUILD)
    async def giveaway_reroll(
        self,
        ctx: SlashContext,
        message_id: str,
        winners: int | None = None,
    ) -> None:
        if not await require_guild(ctx):
            return
        await ctx.defer(ephemeral=True)
        giveaway = await self._repo(ctx.guild_id).get_by_message(message_id)
        if not giveaway or giveaway.id is None:
            await send_error(ctx, "Aucun giveaway trouvé pour ce message.")
            return
        if not giveaway.drawn:
            await send_error(ctx, "Tirez d'abord les gagnants avec /giveaway end.")
            return

        count = winners if winners is not None else giveaway.winners_count
        new_winners = pick_winners(_eligible(giveaway), count, exclude=giveaway.winners)
        if not new_winners:
            await send_error(ctx, "Pas assez de participants pour un nouveau tirage.")
            return

        giveaway.winners = giveaway.winners + new_winners
        await self._repo(ctx.guild_id).update_winners(giveaway.id, giveaway.winners)

        mention = ", ".join(f"<@{w}>" for w in new_winners)
        try:
            message = await self._fetch_message(giveaway)
            if message is not None:
                await message.reply(
                    f"🎉 Reroll : nouveau(x) gagnant(s) du giveaway **{giveaway.prize}** : {mention}",
                    allowed_mentions=_winners_only(new_winners),
                )
        except Exception as e:
            logger.warning("Could not post reroll reply: %s", e)
        await send_success(ctx, f"Nouveau tirage : {mention}")

    @giveaway_start.subcommand(
        sub_cmd_name="list",
        sub_cmd_description="Lister les giveaways en cours",
    )
    async def giveaway_list(self, ctx: SlashContext) -> None:
        if not await require_guild(ctx):
            return
        active = await self._repo(ctx.guild_id).list_active()
        if not active:
            await ctx.send("Aucun giveaway en cours.", ephemeral=True)
            return
        lines: list[str] = []
        for g in active:
            link = f"https://discord.com/channels/{g.guild_id}/{g.channel_id}/{g.message_id}"
            lines.append(
                f"• **{g.prize}** — {g.winners_count} gagnant(s) — "
                f"{len(g.entrants)} participant(s) — "
                f"fin {format_discord_timestamp(g.ends_at, 'R')} [↗]({link})"
            )
        embed = Embed(
            title="Giveaways en cours",
            description=clip("\n".join(lines), EMBED_DESCRIPTION_LIMIT),
            color=Colors.UTIL,
        )
        await ctx.send(embeds=[embed], ephemeral=True)

    @giveaway_end.autocomplete("giveaway")
    @giveaway_cancel.autocomplete("giveaway")
    async def open_giveaway_choices(self, ctx: AutocompleteContext) -> None:
        """The server's open giveaways, soonest end first."""
        if ctx.guild_id is None or not GIVEAWAY.enabled(ctx.guild_id):
            await ctx.send(choices=[])
            return
        active = await self._repo(ctx.guild_id).list_active()
        items = [
            (f"{g.prize} · fin le {clock.to_local(g.ends_at):%d/%m à %H:%M}", g.message_id)
            for g in active
        ]
        await ctx.send(choices=matching_choices(items, ctx.input_text))

    @giveaway_reroll.autocomplete("giveaway")
    async def drawn_giveaway_choices(self, ctx: AutocompleteContext) -> None:
        """The server's most recently drawn giveaways."""
        if ctx.guild_id is None or not GIVEAWAY.enabled(ctx.guild_id):
            await ctx.send(choices=[])
            return
        drawn = await self._repo(ctx.guild_id).list_drawn(MAX_CHOICES)
        items = [
            (f"{g.prize} · clos le {clock.to_local(g.ends_at):%d/%m}", g.message_id) for g in drawn
        ]
        await ctx.send(choices=matching_choices(items, ctx.input_text))

    # ------------------------------------------------------------------
    # Entry buttons
    # ------------------------------------------------------------------

    @component_callback(ENTER_ID)
    async def on_enter(self, ctx: ComponentContext) -> None:
        await self._change_entry(ctx, entering=True)

    @component_callback(LEAVE_ID)
    async def on_leave(self, ctx: ComponentContext) -> None:
        await self._change_entry(ctx, entering=False)

    async def _change_entry(self, ctx: ComponentContext, *, entering: bool) -> None:
        """Enter or withdraw the member who clicked, then refresh the announcement."""
        if ctx.guild_id is None or not GIVEAWAY.enabled(ctx.guild_id):
            await ctx.send("Les giveaways ne sont pas activés sur ce serveur.", ephemeral=True)
            return
        repo = self._repo(ctx.guild_id)
        message_id, user_id = str(ctx.message.id), str(ctx.author.id)
        giveaway = await repo.get_by_message(message_id)
        if giveaway is None:
            await ctx.send("Ce giveaway est introuvable.", ephemeral=True)
            return
        if entering and user_id == giveaway.host_id and not _allow_host(giveaway):
            await ctx.send("L'organisateur ne peut pas participer à ce giveaway.", ephemeral=True)
            return
        change = repo.add_entrant if entering else repo.remove_entrant
        before = await change(message_id, user_id, clock.now())
        if before is None:
            await ctx.send("Ce giveaway est terminé.", ephemeral=True)
            return
        was_in = user_id in before.entrants
        if entering:
            reply = "Tu participes déjà." if was_in else "🎉 Ta participation est enregistrée !"
        else:
            reply = "Ta participation est retirée." if was_in else "Tu ne participais pas."
        await ctx.send(reply, ephemeral=True)
        if was_in != entering:
            message = ctx.message
            self._refreshes.request(
                int(message.id), lambda: self._refresh_participants(message, str(ctx.guild_id))
            )

    async def _refresh_participants(self, message: Message, guild_id: str) -> None:
        """Re-render the participants of the giveaway *message* announces, from the database."""
        giveaway = await self._repo(guild_id).get_by_message(str(message.id))
        if giveaway is None or giveaway.drawn or giveaway.cancelled:
            return
        host_name, host_avatar = _footer_host(message, giveaway)
        embed = _build_embed(
            giveaway,
            host_name=host_name,
            host_avatar=host_avatar,
            entrants=giveaway.entrants,
        )
        try:
            await edit_message_if_changed(
                message, embeds=[embed], components=_entry_buttons(giveaway), logger=logger
            )
        except Exception as e:
            logger.debug("Could not refresh giveaway participants for %s: %s", giveaway.id, e)

    # ------------------------------------------------------------------
    # Background scheduler
    # ------------------------------------------------------------------

    @Task.create(IntervalTrigger(seconds=30))
    async def check_giveaways(self) -> None:
        now = clock.now()
        for gid in GIVEAWAY.guild_ids():
            try:
                due = await self._repo(gid).list_due(now)
            except Exception as e:
                log_failure(logger, "Could not fetch due giveaways for %s: %s", gid, e)
                continue
            for giveaway in due:
                try:
                    await self._draw(giveaway)
                except Exception as e:
                    logger.exception("Unhandled error drawing giveaway %s: %s", giveaway.id, e)

    async def _fetch_message(self, giveaway: Giveaway) -> Message | None:
        """The announcement, or None when it (or its channel) no longer exists.

        Raises when Discord doesn't answer (5xx, timeout), which is not the
        same thing: the giveaway must not close for a passing outage.
        """
        channel: BaseChannel | None = await self.bot.fetch_channel(int(giveaway.channel_id))
        if channel is None or not hasattr(channel, "fetch_message"):
            return None
        return await channel.fetch_message(int(giveaway.message_id))  # type: ignore[union-attr]

    async def _draw(self, giveaway: Giveaway) -> DrawOutcome:
        """Pick winners, edit the announcement, and persist the result.

        The giveaway is claimed first (open → drawn, in one update), so of two
        draws racing (the scheduler and ``/giveaway end`` at the deadline) one
        returns ``"taken"`` without touching anything. When Discord doesn't
        answer, the claim is given back and the next run retries (``"retry"``),
        up to :data:`MAX_DRAW_FAILURES` times.
        """
        if giveaway.id is None:
            return "taken"
        repo = self._repo(giveaway.guild_id)
        claimed = await repo.claim_draw(giveaway.id)
        if claimed is None:
            return "taken"
        giveaway = claimed
        try:
            message = await self._fetch_message(giveaway)
        except Exception as e:
            if giveaway.draw_failures + 1 < MAX_DRAW_FAILURES:
                logger.warning("Could not read giveaway %s, retrying later: %s", giveaway.id, e)
                await repo.release_draw(giveaway.id)
                return "retry"
            log_failure(
                logger,
                "Giveaway %s closed without winners: Discord didn't answer %d times (%s)",
                giveaway.id,
                MAX_DRAW_FAILURES,
                e,
            )
            return "drawn"
        if message is None:
            logger.info("Giveaway %s closed without winners: its message is gone", giveaway.id)
            return "drawn"

        entrants = _eligible(giveaway)
        winners = pick_winners(entrants, giveaway.winners_count)
        await repo.update_winners(giveaway.id, winners)

        mention = ", ".join(f"<@{w}>" for w in winners) if winners else None
        host_name, host_avatar = _footer_host(message, giveaway)
        embed = _build_embed(
            giveaway,
            host_name=host_name,
            host_avatar=host_avatar,
            closed=True,
            winners_mention=mention,
            entry_count=len(entrants),
        )
        try:
            await edit_message_if_changed(message, embeds=[embed], components=[], logger=logger)
        except Exception as e:
            logger.warning("Could not edit closed giveaway %s: %s", giveaway.id, e)

        if mention:
            try:
                await message.reply(
                    f"🎉 Félicitations {mention}! Vous remportez **{giveaway.prize}**.",
                    allowed_mentions=_winners_only(winners),
                )
            except Exception as e:
                logger.warning("Could not post winner reply for %s: %s", giveaway.id, e)
        else:
            try:
                await message.reply("Aucune participation valide — pas de gagnant·e.")
            except Exception as e:
                logger.warning("Could not post empty-draw reply for %s: %s", giveaway.id, e)

        logger.info(
            "Giveaway %s drawn (%d entrants, %d winners)",
            giveaway.id,
            len(entrants),
            len(winners),
        )
        return "drawn"


def setup(bot: Client) -> None:
    GiveawayExtension(bot)


__all__ = ["GiveawayExtension", "setup"]
