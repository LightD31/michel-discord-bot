"""A giveaway's announcement, its draw and its cancellation, shared with the dashboard (P42).

``/concours tirer``, ``/concours annuler``, the scheduler and the module page's
"Tirer maintenant" / "Annuler" all go through :func:`draw` and :func:`cancel`:
claim the giveaway in one conditional update, then edit its announcement.
"""

from __future__ import annotations

from typing import Any, Literal

from interactions import AllowedMentions, BaseChannel, Embed, Message

from features.giveaway import Giveaway, GiveawayRepository, pick_winners
from src.core import logging as logutil
from src.core.config import ModuleConfig
from src.core.logging import log_failure
from src.core.text import format_discord_timestamp
from src.discord_ext.channels import fetch_guild_channel
from src.discord_ext.look import Kind, card
from src.discord_ext.messages import edit_message_if_changed

logger = logutil.init_logger(__name__)

GIVEAWAY = ModuleConfig("moduleGiveaway")
DEFAULT_ALLOW_HOST_WIN = False
# Affiche les mentions tant que la liste reste lisible dans un champ d'embed.
MAX_LISTED_PARTICIPANTS = 15
MAX_PARTICIPANTS_FIELD_CHARS = 900
# A draw Discord doesn't answer for (5xx, timeout) is retried at the next runs
# (every 30 s), then the giveaway closes without winners.
MAX_DRAW_FAILURES = 10
# The announcement's footer names its host after Michel's: "Michel · Giveaways · Lancé par alice".
HOST_MARK = "Lancé par "

DrawOutcome = Literal["drawn", "taken", "retry"]


def guild_allow_host_win(guild_id: str | int | None) -> bool:
    """Return whether the giveaway host can be part of the draw pool."""
    if guild_id is None:
        return DEFAULT_ALLOW_HOST_WIN
    raw = GIVEAWAY.get(guild_id).get("allowHostWin")
    return raw if isinstance(raw, bool) else DEFAULT_ALLOW_HOST_WIN


def winners_only(winners: list[str]) -> AllowedMentions:
    """Ping the winners and nothing else: the prize is whatever the host typed."""
    return AllowedMentions(users=[int(w) for w in winners])


def entry_conditions(giveaway: Giveaway) -> str | None:
    """« Rôle @x · niveau 5 ou plus », or None for a giveaway open to all."""
    parts = []
    if giveaway.required_role_id:
        parts.append(f"Rôle <@&{giveaway.required_role_id}>")
    if giveaway.min_level:
        parts.append(f"niveau {giveaway.min_level} ou plus")
    if not parts:
        return None
    text = " · ".join(parts)
    return text[0].upper() + text[1:]


def participants_field_value(entrant_ids: list[str]) -> str:
    """Format the participants field as mentions, or fallback to a count."""
    count = len(entrant_ids)
    if count == 0:
        return "Aucun"

    mentions = ", ".join(f"<@{uid}>" for uid in entrant_ids)
    if count > MAX_LISTED_PARTICIPANTS or len(mentions) > MAX_PARTICIPANTS_FIELD_CHARS:
        suffix = "participant·e" if count == 1 else "participant·e·s"
        return f"{count} {suffix}"
    return mentions


def build_embed(
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
    kind: Kind = "info"
    if cancelled:
        title_prefix = "🚫 GIVEAWAY ANNULÉ"
        kind = "neutral"
    elif closed:
        title_prefix = "🏁 GIVEAWAY TERMINÉ"
        kind = "success"

    embed = card(
        "moduleGiveaway",
        kind,
        title=f"{title_prefix} — {giveaway.prize}",
        footer=f"{HOST_MARK}{host_name}",
        footer_icon=host_avatar,
    )

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
    conditions = entry_conditions(giveaway)
    if conditions and not cancelled and not closed:
        embed.add_field(name="Pour participer", value=conditions, inline=True)
    participants_value: str | None = None
    if entrants is not None:
        participants_value = participants_field_value(entrants)
    elif entry_count is not None:
        participants_value = str(entry_count)
    if participants_value is not None:
        embed.add_field(name="Participants", value=participants_value, inline=True)

    return embed


def footer_host(message: Message | None, giveaway: Giveaway) -> tuple[str, str | None]:
    """The host name and avatar the announcement's footer shows, to keep them on a re-render."""
    host_name = f"ID:{giveaway.host_id}"
    host_avatar: str | None = None
    if message is None or not message.embeds:
        return host_name, host_avatar
    footer = getattr(message.embeds[0], "footer", None)
    footer_text = getattr(footer, "text", None)
    if isinstance(footer_text, str) and HOST_MARK in footer_text:
        resolved = footer_text.split(HOST_MARK, 1)[1].strip()
        if resolved:
            host_name = resolved
    footer_icon = getattr(footer, "icon_url", None)
    if isinstance(footer_icon, str) and footer_icon:
        host_avatar = footer_icon
    return host_name, host_avatar


def allow_host(giveaway: Giveaway) -> bool:
    """Whether the host may win: the giveaway's own setting, else the server's."""
    if giveaway.allow_host_win is not None:
        return giveaway.allow_host_win
    return guild_allow_host_win(giveaway.guild_id)


def eligible(giveaway: Giveaway) -> list[str]:
    """The entrants who may win: the host only where the giveaway allows it."""
    if allow_host(giveaway):
        return list(giveaway.entrants)
    return [uid for uid in giveaway.entrants if uid != giveaway.host_id]


async def fetch_message(bot: Any, giveaway: Giveaway) -> Message | None:
    """The announcement, or None when it (or its channel) no longer exists.

    Raises when Discord doesn't answer (5xx, timeout), which is not the
    same thing: the giveaway must not close for a passing outage.
    """
    channel: BaseChannel | None = await fetch_guild_channel(
        bot, giveaway.guild_id, giveaway.channel_id, raise_outages=True
    )
    if channel is None or not hasattr(channel, "fetch_message"):
        return None
    return await channel.fetch_message(int(giveaway.message_id))


async def still_eligible(bot: Any, giveaway: Giveaway, user_id: str) -> bool:
    """Whether *user_id* is still on the server, with the required role, at the draw.

    Discord not answering keeps them in: an outage is no reason to lose a prize.
    """
    guild = bot.get_guild(int(giveaway.guild_id))
    if guild is None:
        return True
    try:
        member = guild.get_member(int(user_id)) or await guild.fetch_member(int(user_id))
    except Exception as e:
        logger.debug("Could not read entrant %s of %s: %s", user_id, giveaway.id, e)
        return True
    if member is None:
        return False
    role_id = giveaway.required_role_id
    return not role_id or any(str(getattr(r, "id", r)) == role_id for r in member.roles or [])


async def pick_present(
    bot: Any, giveaway: Giveaway, count: int, *, exclude: list[str] | None = None
) -> list[str]:
    """Up to *count* winners among the entrants still eligible (P14).

    The entrants are shuffled, then checked one by one until enough are
    found: one member lookup per entrant drawn, not one per entrant.
    """
    shuffled = pick_winners(eligible(giveaway), len(giveaway.entrants), exclude=exclude)
    winners: list[str] = []
    for uid in shuffled:
        if len(winners) == count:
            break
        if await still_eligible(bot, giveaway, uid):
            winners.append(uid)
    return winners


async def draw(bot: Any, repo: GiveawayRepository, giveaway: Giveaway) -> DrawOutcome:
    """Pick winners, edit the announcement, and persist the result.

    The giveaway is claimed first (open → drawn, in one update), so of two
    draws racing (the scheduler, ``/concours tirer`` and the dashboard at the
    deadline) one returns ``"taken"`` without touching anything. When Discord
    doesn't answer, the claim is given back and the next run retries
    (``"retry"``), up to :data:`MAX_DRAW_FAILURES` times.
    """
    if giveaway.id is None:
        return "taken"
    claimed = await repo.claim_draw(giveaway.id)
    if claimed is None:
        return "taken"
    giveaway = claimed
    try:
        message = await fetch_message(bot, giveaway)
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

    entrants = eligible(giveaway)
    winners = await pick_present(bot, giveaway, giveaway.winners_count)
    await repo.update_winners(giveaway.id, winners)

    mention = ", ".join(f"<@{w}>" for w in winners) if winners else None
    host_name, host_avatar = footer_host(message, giveaway)
    embed = build_embed(
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
                f"🎉 Félicitations {mention} ! Tu remportes **{giveaway.prize}**.",
                # Message.reply() is annotated with a dict; send() takes either.
                allowed_mentions=winners_only(winners),  # type: ignore[arg-type]
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


async def cancel(bot: Any, repo: GiveawayRepository, giveaway: Giveaway) -> bool:
    """Cancel an open giveaway and mark its announcement; False when it closed meanwhile."""
    if giveaway.id is None or not await repo.mark_cancelled(giveaway.id):
        return False
    giveaway = giveaway.model_copy(update={"cancelled": True})
    try:
        message = await fetch_message(bot, giveaway)
        if message is not None:
            host_name, host_avatar = footer_host(message, giveaway)
            embed = build_embed(
                giveaway, host_name=host_name, host_avatar=host_avatar, cancelled=True
            )
            await edit_message_if_changed(message, embeds=[embed], components=[], logger=logger)
    except Exception as e:
        logger.warning("Could not edit cancelled giveaway message: %s", e)
    return True


__all__ = [
    "DEFAULT_ALLOW_HOST_WIN",
    "GIVEAWAY",
    "HOST_MARK",
    "MAX_DRAW_FAILURES",
    "DrawOutcome",
    "allow_host",
    "build_embed",
    "cancel",
    "draw",
    "eligible",
    "entry_conditions",
    "fetch_message",
    "footer_host",
    "guild_allow_host_win",
    "pick_present",
    "still_eligible",
    "winners_only",
]
