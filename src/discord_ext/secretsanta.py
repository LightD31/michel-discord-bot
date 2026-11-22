"""The Secret Santa draw and the messages around it, shared with the dashboard.

``/secretsanta tirage`` and the dashboard's "Tirer au sort" both run
:func:`run_draw`: claim the session, send each Santa their DM (recipient, wish
list, budget, date, and the buttons of the session), store the results and
close the announcement. The extension's buttons are the custom ids below.

No custom id ever carries a member id: a member can read the ids of the
buttons they are sent, so the recipient's "Répondre" names the session only,
and the bot finds their Santa from the draw.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from interactions import ActionRow, Button, ButtonStyle, Client, Embed, spread_to_rows

from features.secretsanta import (
    SecretSantaRepository,
    SecretSantaSession,
    generate_assignments_with_subgroups,
    generate_valid_assignments,
)
from features.secretsanta.dates import format_exchange_date
from src.core import clock
from src.core import logging as logutil
from src.core.config import load_discord2name
from src.core.logging import log_failure
from src.core.text import clip
from src.discord_ext.channels import fetch_guild_channel
from src.discord_ext.look import card
from src.discord_ext.messages import edit_message_if_changed, fetch_user_safe

logger = logutil.init_logger(__name__)

DATA_DIR = Path("data/secret_santa")
WISHLIST_LIMIT = 1000

JOIN = "secretsanta_join"
LEAVE = "secretsanta_leave"
WISHLIST = "secretsanta_wishlist"
WRITE = "secretsanta_write"  # the Santa writes to their recipient
REPLY = "secretsanta_reply"  # the recipient answers their Santa
REPORT = "secretsanta_report"  # :santa (a message from the Santa) or :recipient
SENT = "secretsanta_sent"
RECEIVED = "secretsanta_received"


def context_guild_id(context_id: str) -> str | None:
    """The guild of a ``guild_<id>`` session, ``None`` for a group DM's."""
    return context_id.removeprefix("guild_") if context_id.startswith("guild_") else None


def join_buttons(context_id: str, disabled: bool = False) -> list[ActionRow]:
    """Participer / Ma liste / Se retirer, under the session's announcement."""
    return spread_to_rows(
        Button(
            style=ButtonStyle.SUCCESS,
            label="Participer 🎁",
            custom_id=f"{JOIN}:{context_id}",
            disabled=disabled,
        ),
        Button(
            style=ButtonStyle.SECONDARY,
            label="📝 Ma liste",
            custom_id=f"{WISHLIST}:{context_id}",
            disabled=disabled,
        ),
        Button(
            style=ButtonStyle.DANGER,
            label="Se retirer",
            custom_id=f"{LEAVE}:{context_id}",
            disabled=disabled,
        ),
    )


def session_details(session: SecretSantaSession) -> str:
    """The budget, date and parcel lines of a session (may be empty)."""
    lines = []
    if session.budget:
        lines.append(f"💰 **Budget suggéré :** {session.budget}")
    if session.exchange_day:
        lines.append(f"📅 **Échange :** {format_exchange_date(session.exchange_day)}")
    if session.shipping:
        lines.append("📦 **Cadeaux envoyés par colis**")
    return "\n".join(lines)


def announcement_embed(session: SecretSantaSession, participants: list[str] | None) -> Embed:
    """The session's announcement; *participants* is ``None`` in a group DM."""
    description = (
        "🎄 **Une session de Père Noël Secret est ouverte !** 🎄\n\n"
        "Clique sur **Participer** pour rejoindre le tirage au sort : tu pourras "
        "y laisser ta liste d'envies, et la modifier avec **📝 Ma liste** "
        "jusqu'au tirage. Tu peux te retirer à tout moment avant le tirage.\n\n"
    )
    details = session_details(session)
    if details:
        description += details + "\n"
    if participants is None:
        description += "\nLa liste des inscrits : `/secretsanta participants`."
    else:
        listed = "\n".join(f"• {m}" for m in participants) or "*Aucun participant pour le moment*"
        description += f"\n**Participants ({len(participants)}) :**\n{listed}"
    return card(
        "moduleSecretSanta",
        title="🎅 Père Noël Secret",
        description=clip(description, 4096),
    )


def gift_buttons(context_id: str, *, relay: bool, shipping: bool) -> list[ActionRow]:
    """The buttons of a Santa's draw DM: write anonymously, and the parcel marks."""
    buttons = []
    if relay:
        buttons.append(
            Button(
                style=ButtonStyle.PRIMARY,
                label="✉️ Écrire anonymement",
                custom_id=f"{WRITE}:{context_id}",
            )
        )
    if shipping:
        buttons += [
            Button(
                style=ButtonStyle.SUCCESS,
                label="📦 Cadeau envoyé",
                custom_id=f"{SENT}:{context_id}",
            ),
            Button(
                style=ButtonStyle.SECONDARY,
                label="🎁 Cadeau reçu",
                custom_id=f"{RECEIVED}:{context_id}",
            ),
        ]
    return spread_to_rows(*buttons) if buttons else []


def draw_dm_embed(
    session: SecretSantaSession,
    receiver_name: str,
    wishlist: str | None,
    *,
    relay: bool,
    title: str = "🎅 Père Noël Secret",
    footer: str | None = None,
) -> Embed:
    """What a Santa is told: who, their wish list, the budget and the date."""
    parts = [
        "🎄 Ho, ho, ho ! C'est le Père Noël ! 🎄",
        f"Cette année, tu offres un cadeau à **{receiver_name}** !\n"
        "À toi de voir s'il/elle a été sage... 😉",
    ]
    if wishlist:
        parts.append(f"📝 **Sa liste d'envies :**\n{clip(wishlist, WISHLIST_LIMIT + 10)}")
    else:
        hint = " Écris-lui anonymement pour lui demander !" if relay else ""
        parts.append(f"📝 *Pas de liste d'envies.*{hint}")
    details = session_details(session)
    if details:
        parts.append(details)
    if session.shipping:
        parts.append(
            "Quand ton colis est parti, clique sur **📦 Cadeau envoyé** : ton destinataire "
            "est prévenu. Quand le tien arrive, clique sur **🎁 Cadeau reçu**."
        )
    parts.append("*Signé : Le vrai Père Noël* 🎅")
    return card("moduleSecretSanta", title=title, description="\n\n".join(parts), footer=footer)


def receiver_display_name(guild_id: str | None, user_id: int, user: Any) -> str:
    """The first name the server gave *user_id*, else their mention."""
    names = load_discord2name(guild_id) if guild_id else {}
    return str(names.get(str(user_id), user.mention if user else f"<@{user_id}>"))


@dataclass
class DrawOutcome:
    """What :func:`run_draw` did: ``error`` (French) when nothing was drawn."""

    error: str | None = None
    count: int = 0
    subgroups: int = 1
    failed_dms: list[int] = field(default_factory=list)


IMPOSSIBLE = "Impossible de générer un tirage valide avec les restrictions actuelles."
ALREADY_DRAWN = "Le tirage au sort a déjà été effectué pour cette session !"


async def run_draw(
    bot: Client,
    repo: SecretSantaRepository,
    session: SecretSantaSession,
    *,
    allow_subgroups: bool,
    relay: bool,
    context_name: str,
    announce_channel: Any = None,
) -> DrawOutcome:
    """Draw *session*, DM every Santa, store the results and close the announcement.

    *announce_channel* is where a group DM's session posts its result (a
    guild's edits its announcement instead). Call it on the bot loop.
    """
    if session.is_drawn:
        return DrawOutcome(error=ALREADY_DRAWN)
    if len(session.participants) < 2:
        return DrawOutcome(
            error="Il faut au moins 2 participants pour le tirage.\n"
            f"Participants actuels : {len(session.participants)}"
        )
    context_id = session.context_id
    banned_pairs = await repo.read_banned_pairs(context_id)
    subgroups = 1
    assignments = generate_valid_assignments(session.participants, banned_pairs)
    if not assignments and allow_subgroups:
        result = generate_assignments_with_subgroups(session.participants, banned_pairs)
        if result:
            assignments, subgroups = result
    if not assignments:
        return DrawOutcome(error=IMPOSSIBLE)
    if not await repo.claim_draw(context_id):
        return DrawOutcome(error=ALREADY_DRAWN)
    session.is_drawn = True

    guild_id = context_guild_id(context_id)
    wishlists = await repo.wishlists(context_id)
    components = gift_buttons(context_id, relay=relay, shipping=session.shipping)
    failed: list[int] = []
    for giver_id, receiver_id in assignments:
        try:
            _, giver = await fetch_user_safe(bot, giver_id)
            _, receiver = await fetch_user_safe(bot, receiver_id)
            embed = draw_dm_embed(
                session,
                receiver_display_name(guild_id, receiver_id, receiver),
                wishlists.get(receiver_id),
                relay=relay,
                footer=context_name,
            )
            await giver.send(embed=embed, components=components)
        except Exception as e:
            log_failure(logger, "Failed to send the Secret Santa DM to %s: %s", giver_id, e)
            failed.append(giver_id)

    await repo.save_draw_results(context_id, assignments)
    await save_human_readable_draw(bot, assignments, session, context_name)
    await _announce(bot, session, guild_id, announce_channel)
    logger.info("Secret Santa drawn in %s (%s subgroup(s))", context_id, subgroups)
    return DrawOutcome(count=len(session.participants), subgroups=subgroups, failed_dms=failed)


async def _announce(
    bot: Client, session: SecretSantaSession, guild_id: str | None, announce_channel: Any
) -> None:
    mentions = []
    for uid in session.participants:
        _, user = await fetch_user_safe(bot, uid)
        mentions.append(user.mention if user else f"<@{uid}>")
    embed = card(
        "moduleSecretSanta",
        title="🎅 Tirage effectué ! 🎉",
        description=clip(
            f"Le tirage au sort a été effectué pour **{len(session.participants)}** "
            "participants !\n\n**Participants :**\n"
            + "\n".join(f"• {m}" for m in mentions)
            + "\n\nChaque participant vient de recevoir en MP le nom de la personne à gâter ! 🎁",
            4096,
        ),
    )
    try:
        if guild_id is None:
            if announce_channel is not None:
                await announce_channel.send(embed=embed)
            return
        if not session.message_id:
            return
        channel = await fetch_guild_channel(bot, guild_id, session.channel_id, logger=logger)
        if channel is None:
            return
        message = await channel.fetch_message(session.message_id)
        if message:
            await edit_message_if_changed(
                message,
                embed=embed,
                components=join_buttons(session.context_id, disabled=True),
                logger=logger,
            )
    except Exception as e:
        log_failure(logger, "Failed to announce the Secret Santa draw: %s", e)


async def save_human_readable_draw(
    bot: Client,
    assignments: list[tuple[int, int]],
    session: SecretSantaSession,
    context_name: str,
) -> None:
    """A text copy of the draw for the Operator, under ``data/secret_santa``."""
    stamp = clock.local_now()
    lines = [
        "=" * 50,
        "🎅 PÈRE NOËL SECRET - RÉSULTATS DU TIRAGE 🎅",
        "=" * 50,
        "",
        f"📅 Date du tirage : {stamp.strftime('%d/%m/%Y à %H:%M')}",
        f"📍 Contexte : {context_name}",
        f"👥 Nombre de participants : {len(assignments)}",
    ]
    if session.budget:
        lines.append(f"💰 Budget suggéré : {session.budget}")
    if session.exchange_day:
        lines.append(f"📆 Échange : {format_exchange_date(session.exchange_day)}")
    lines.extend(["", "-" * 50, "ATTRIBUTIONS :", "-" * 50, ""])
    for giver_id, receiver_id in assignments:
        names = []
        for uid in (giver_id, receiver_id):
            _, user = await fetch_user_safe(bot, uid)
            names.append(f"{user.display_name} (@{user.username})" if user else f"#{uid}")
        lines.append(f"  🎁 {names[0]}  →  {names[1]}")
    lines.extend(["", "=" * 50, "Fichier généré automatiquement par Michel Bot", "=" * 50])
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = DATA_DIR / (
            f"draw_{session.context_id.replace(':', '_')}_{stamp.strftime('%Y%m%d_%H%M%S')}.txt"
        )
        path.write_text("\n".join(lines), encoding="utf-8")
    except OSError as e:
        log_failure(logger, "Could not write the Secret Santa draw file: %s", e)


async def participant_mentions(bot: Any, session: SecretSantaSession) -> list[str]:
    mentions = []
    for user_id in session.participants:
        _, user = await fetch_user_safe(bot, user_id)
        mentions.append(user.mention if user else f"<@{user_id}>")
    return mentions


async def refresh_announcement(bot: Any, session: SecretSantaSession) -> None:
    """Show the session's current participants and details on its announcement.

    Shared by the join and leave buttons and the member space (P16), on the bot loop.
    """
    guild_id = context_guild_id(session.context_id)
    if guild_id is None or not session.message_id or session.is_drawn:
        return
    try:
        channel = await fetch_guild_channel(bot, guild_id, session.channel_id)
        if channel is None:
            return
        message = await channel.fetch_message(session.message_id)
        if message:
            await edit_message_if_changed(
                message,
                embed=announcement_embed(session, await participant_mentions(bot, session)),
                components=join_buttons(session.context_id),
                logger=logger,
            )
    except Exception as e:
        log_failure(logger, "Failed to update the Secret Santa announcement: %s", e)


__all__ = [
    "participant_mentions",
    "refresh_announcement",
    "DATA_DIR",
    "DrawOutcome",
    "announcement_embed",
    "context_guild_id",
    "draw_dm_embed",
    "gift_buttons",
    "join_buttons",
    "receiver_display_name",
    "run_draw",
    "session_details",
]
