"""Giveaway Discord extension — ``/giveaway`` slash command + draw scheduler.

Slash commands:
- ``/giveaway start prize:<str> duration:<30m|2h|1d> winners:<int> [allow_host_win:<bool>]`` — post a
  giveaway embed and start accepting reaction entries.
- ``/giveaway end message_id:<id>`` — end a giveaway early and draw winners now.
- ``/giveaway reroll message_id:<id> [winners:<int>]`` — pick fresh winners
  excluding any prior winners.
- ``/giveaway list`` — list active giveaways on this guild.
- ``/giveaway cancel message_id:<id>`` — cancel without drawing.

Reaction-based entry: users click the configured emoji (🎉 by default).
A background task scans every 30 s for due giveaways and draws winners.
Persistence: per-guild ``giveaways`` collection. Enabled per-guild via
``moduleGiveaway``.
"""

from __future__ import annotations

from asyncio import Lock
from datetime import datetime, timedelta

from interactions import (
    BaseChannel,
    Client,
    Embed,
    Extension,
    IntervalTrigger,
    Message,
    OptionType,
    Permissions,
    SlashContext,
    Task,
    listen,
    slash_command,
    slash_default_member_permission,
    slash_option,
)
from interactions.api.events import MessageReactionAdd, MessageReactionRemove

from features.giveaway import MAX_WINNERS, Giveaway, GiveawayRepository, pick_winners
from features.polls import parse_duration  # reused: same DSL as /poll
from src.discord_ext.embeds import Colors, format_discord_timestamp
from src.discord_ext.messages import require_guild, send_error, send_success

from ._common import (
    enabled_servers,
    enabled_servers_int,
    guild_allow_host_win,
    guild_emoji,
    logger,
)

# Floor on giveaway duration. Anything shorter than 10 s is almost certainly a
# typo and races the background scheduler.
MIN_DURATION_SECONDS = 10
# Hard ceiling — 30 days. Discord stops surfacing reactions on very old
# messages, so longer giveaways risk silent entry loss.
MAX_DURATION_SECONDS = 30 * 86400
# Affiche les mentions tant que la liste reste lisible dans un champ d'embed.
MAX_LISTED_PARTICIPANTS = 15
MAX_PARTICIPANTS_FIELD_CHARS = 900


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
        description_lines.append(f"Réagissez avec {giveaway.emoji} pour participer !")
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


async def _collect_entrants(
    message: Message,
    emoji: str,
    host_id: str,
    *,
    allow_host: bool,
) -> list[str]:
    """Read reactions from *message* and return valid entrant IDs."""
    target = next(
        (r for r in (message.reactions or []) if str(getattr(r.emoji, "name", r.emoji)) == emoji),
        None,
    )
    if target is None:
        return []
    entrants: list[str] = []
    seen: set[str] = set()
    try:
        async for user in target.users():
            if getattr(user, "bot", False):
                continue
            uid = str(user.id)
            if (not allow_host and uid == host_id) or uid in seen:
                continue
            seen.add(uid)
            entrants.append(uid)
    except Exception as e:
        logger.warning("Could not iterate reactions on %s: %s", message.id, e)
    return entrants


class GiveawayExtension(Extension):
    def __init__(self, bot: Client):
        self.bot: Client = bot
        self._repos: dict[str, GiveawayRepository] = {}
        self._reaction_lock = Lock()

    def _repo(self, guild_id: str | int) -> GiveawayRepository:
        gid = str(guild_id)
        repo = self._repos.get(gid)
        if repo is None:
            repo = GiveawayRepository(gid)
            self._repos[gid] = repo
        return repo

    @listen()
    async def on_startup(self) -> None:
        for gid in enabled_servers:
            try:
                await self._repo(gid).ensure_indexes()
            except Exception as e:
                logger.error("Could not init giveaway indexes for %s: %s", gid, e)
        self.check_giveaways.start()
        logger.info("Giveaway extension ready (%d guild(s))", len(enabled_servers))

    # ------------------------------------------------------------------
    # Slash commands
    # ------------------------------------------------------------------

    @slash_command(
        name="giveaway",
        description="Gérer les giveaways",
        sub_cmd_name="start",
        sub_cmd_description="Lancer un giveaway avec entrée par réaction",
        scopes=enabled_servers_int,  # type: ignore[arg-type]
    )
    @slash_option(
        "prize",
        "Lot mis en jeu",
        opt_type=OptionType.STRING,
        required=True,
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

        ends_at = datetime.now() + timedelta(seconds=seconds)
        emoji = guild_emoji(ctx.guild_id)
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
            emoji=emoji,
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
        message = await ctx.send(embeds=[embed])
        giveaway.message_id = str(message.id)

        try:
            giveaway.id = await self._repo(ctx.guild_id).add(giveaway)
        except Exception as e:
            logger.error("Could not persist giveaway: %s", e)
            await send_error(ctx, "Le giveaway a été posté mais sa persistance a échoué.")
            return
        try:
            await message.add_reaction(emoji)
        except Exception as e:
            logger.warning("Could not seed reaction %s on %s: %s", emoji, message.id, e)

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
        "message_id",
        "ID du message du giveaway",
        opt_type=OptionType.STRING,
        required=True,
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
        await self._draw(giveaway)
        await send_success(ctx, "Tirage effectué.")

    @giveaway_start.subcommand(
        sub_cmd_name="cancel",
        sub_cmd_description="Annuler un giveaway en cours sans tirage",
    )
    @slash_option(
        "message_id",
        "ID du message du giveaway",
        opt_type=OptionType.STRING,
        required=True,
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
        await self._repo(ctx.guild_id).mark_cancelled(giveaway.id)
        giveaway.cancelled = True
        message = await self._fetch_message(giveaway)
        if message is not None:
            embed = _build_embed(
                giveaway,
                host_name=ctx.user.username,
                host_avatar=None,
                cancelled=True,
            )
            try:
                await message.edit(embeds=[embed])
            except Exception as e:
                logger.warning("Could not edit cancelled giveaway message: %s", e)
        await send_success(ctx, "Giveaway annulé.")

    @giveaway_start.subcommand(
        sub_cmd_name="reroll",
        sub_cmd_description="Tirer de nouveaux gagnants pour un giveaway clos",
    )
    @slash_option(
        "message_id",
        "ID du message du giveaway",
        opt_type=OptionType.STRING,
        required=True,
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

        message = await self._fetch_message(giveaway)
        if message is None:
            await send_error(ctx, "Message original introuvable.")
            return

        allow_host = (
            giveaway.allow_host_win
            if giveaway.allow_host_win is not None
            else guild_allow_host_win(ctx.guild_id)
        )
        entrants = await _collect_entrants(
            message,
            giveaway.emoji,
            giveaway.host_id,
            allow_host=allow_host,
        )
        count = winners if winners is not None else giveaway.winners_count
        new_winners = pick_winners(entrants, count, exclude=giveaway.winners)
        if not new_winners:
            await send_error(ctx, "Pas assez de participants pour un nouveau tirage.")
            return

        giveaway.winners = giveaway.winners + new_winners
        await self._repo(ctx.guild_id).update_winners(giveaway.id, giveaway.winners)

        mention = ", ".join(f"<@{w}>" for w in new_winners)
        try:
            await message.reply(
                f"🎉 Reroll : nouveau(x) gagnant(s) du giveaway **{giveaway.prize}** : {mention}"
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
                f"fin {format_discord_timestamp(g.ends_at, 'R')} [↗]({link})"
            )
        embed = Embed(
            title="Giveaways en cours",
            description="\n".join(lines)[:4000],
            color=Colors.UTIL,
        )
        await ctx.send(embeds=[embed], ephemeral=True)

    # ------------------------------------------------------------------
    # Live participant updates on reactions
    # ------------------------------------------------------------------

    @listen(MessageReactionAdd)
    async def on_message_reaction_add(self, event: MessageReactionAdd) -> None:
        await self._refresh_participants_embed(event)

    @listen(MessageReactionRemove)
    async def on_message_reaction_remove(self, event: MessageReactionRemove) -> None:
        await self._refresh_participants_embed(event)

    async def _refresh_participants_embed(
        self,
        event: MessageReactionAdd | MessageReactionRemove,
    ) -> None:
        message = event.message
        if message is None or message.guild is None:
            return

        giveaway = await self._repo(message.guild.id).get_by_message(str(message.id))
        if giveaway is None or giveaway.drawn or giveaway.cancelled:
            return

        # Ignore unrelated emojis to avoid editing on every reaction event.
        emoji_name = str(getattr(event.emoji, "name", event.emoji))
        if emoji_name != giveaway.emoji and str(event.emoji) != giveaway.emoji:
            return

        async with self._reaction_lock:
            allow_host = (
                giveaway.allow_host_win
                if giveaway.allow_host_win is not None
                else guild_allow_host_win(message.guild.id)
            )
            entrants = await _collect_entrants(
                message,
                giveaway.emoji,
                giveaway.host_id,
                allow_host=allow_host,
            )

            host_name = f"ID:{giveaway.host_id}"
            host_avatar: str | None = None
            if message.embeds:
                footer = getattr(message.embeds[0], "footer", None)
                footer_text = getattr(footer, "text", None)
                if isinstance(footer_text, str) and footer_text.startswith("Lancé par "):
                    resolved = footer_text.removeprefix("Lancé par ").strip()
                    if resolved:
                        host_name = resolved
                footer_icon = getattr(footer, "icon_url", None)
                if isinstance(footer_icon, str) and footer_icon:
                    host_avatar = footer_icon

            embed = _build_embed(
                giveaway,
                host_name=host_name,
                host_avatar=host_avatar,
                entrants=entrants,
            )
            try:
                await message.edit(embeds=[embed])
            except Exception as e:
                logger.debug("Could not refresh giveaway participants for %s: %s", giveaway.id, e)

    # ------------------------------------------------------------------
    # Background scheduler
    # ------------------------------------------------------------------

    @Task.create(IntervalTrigger(seconds=30))
    async def check_giveaways(self) -> None:
        now = datetime.now()
        for gid in enabled_servers:
            try:
                due = await self._repo(gid).list_due(now)
            except Exception as e:
                logger.error("Could not fetch due giveaways for %s: %s", gid, e)
                continue
            for giveaway in due:
                try:
                    await self._draw(giveaway)
                except Exception as e:
                    logger.exception("Unhandled error drawing giveaway %s: %s", giveaway.id, e)

    async def _fetch_message(self, giveaway: Giveaway) -> Message | None:
        try:
            channel: BaseChannel = await self.bot.fetch_channel(int(giveaway.channel_id))
        except Exception as e:
            logger.warning("Could not fetch giveaway channel %s: %s", giveaway.channel_id, e)
            return None
        if not hasattr(channel, "fetch_message"):
            return None
        try:
            return await channel.fetch_message(int(giveaway.message_id))  # type: ignore[union-attr]
        except Exception as e:
            logger.warning("Could not fetch giveaway message %s: %s", giveaway.message_id, e)
            return None

    async def _draw(self, giveaway: Giveaway) -> None:
        """Pick winners, edit the announcement, and persist the result."""
        if giveaway.id is None:
            return
        message = await self._fetch_message(giveaway)
        if message is None:
            # Mark drawn anyway so we don't keep retrying a deleted message.
            await self._repo(giveaway.guild_id).mark_drawn(giveaway.id, [])
            return

        allow_host = (
            giveaway.allow_host_win
            if giveaway.allow_host_win is not None
            else guild_allow_host_win(giveaway.guild_id)
        )
        entrants = await _collect_entrants(
            message,
            giveaway.emoji,
            giveaway.host_id,
            allow_host=allow_host,
        )
        winners = pick_winners(entrants, giveaway.winners_count)
        await self._repo(giveaway.guild_id).mark_drawn(giveaway.id, winners)

        mention = ", ".join(f"<@{w}>" for w in winners) if winners else None
        embed = _build_embed(
            giveaway,
            host_name=f"ID:{giveaway.host_id}",
            host_avatar=None,
            closed=True,
            winners_mention=mention,
            entry_count=len(entrants),
        )
        try:
            await message.edit(embeds=[embed])
        except Exception as e:
            logger.warning("Could not edit closed giveaway %s: %s", giveaway.id, e)

        if mention:
            try:
                await message.reply(
                    f"🎉 Félicitations {mention}! Vous remportez **{giveaway.prize}**."
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


def setup(bot: Client) -> None:
    GiveawayExtension(bot)


__all__ = ["GiveawayExtension", "setup"]
