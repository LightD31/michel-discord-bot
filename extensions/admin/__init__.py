"""Admin Discord extension — general-purpose moderator utilities.

Slash commands:
- ``/ping`` — bot latency probe
- ``/effacer`` — bulk-delete messages in a channel
- ``/envoyer`` — send a message to a channel as the bot
- ``/mode-lent`` — set or clear a channel's slowmode delay
- ``/verrouiller`` — deny @everyone send permissions on a channel
- ``/deverrouiller`` — restore @everyone send permissions on a channel

Enablement is shared with :mod:`extensions.polls` and
:mod:`extensions.reminders` via the ``moduleUtils`` config key. This package
owns the :class:`UtilsConfig` schema registration.
"""

import os
from typing import cast

from interactions import (
    BaseChannel,
    Buckets,
    ChannelType,
    Client,
    Extension,
    GuildChannel,
    GuildText,
    OptionType,
    Permissions,
    SlashContext,
    cooldown,
    slash_command,
    slash_default_member_permission,
    slash_option,
)

from features.reminders.schedule import MAX_SNOOZE_MINUTES
from src.core import logging as logutil
from src.core.config import ModuleConfig
from src.discord_ext.errors import failure_reply
from src.discord_ext.mentions import mentions_member_could_send
from src.discord_ext.messages import (
    CAN_MANAGE,
    CAN_PURGE,
    CAN_SEND,
    author_can,
    send_error,
    send_success,
)
from src.discord_ext.scopes import guild_scopes
from src.webui.schemas import ModuleHelp, SchemaBase, enabled_field, register_module, ui


@register_module("moduleUtils")
class UtilsConfig(SchemaBase):
    __label__ = "Utilitaires"
    __description__ = "Commandes utilitaires : ping, sondages, rappels, suppression de messages."
    __icon__ = "🛠️"
    __category__ = "Outils"
    __help__ = ModuleHelp(
        "Les outils du quotidien : sondages, rappels, messages et salons.",
        ("/sondage", "/rappel creer", "/rappel liste"),
        "Un sondage public est un sondage Discord ; anonyme ou par classement, il a des "
        "boutons. Un rappel arrive en MP à l'heure dite (« samedi 9h », « dans 2h »). "
        "Clic droit sur un message → ⏰ Me le rappeler.",
    )

    enabled: bool = enabled_field()
    reminderSnoozeMinutes: int = ui(
        "Durée du snooze (rappels)",
        "number",
        ge=1,
        le=MAX_SNOOZE_MINUTES,
        default=10,
        description=(
            "Délai en minutes appliqué quand un membre clique sur le bouton "
            "« Snooze » d'un rappel reçu en message privé."
        ),
    )
    pollDefaultDurationMinutes: int = ui(
        "Durée par défaut des sondages",
        "number",
        ge=0,
        le=30 * 24 * 60,
        default=0,
        description=(
            "Si > 0, un `/sondage` dont la durée est laissée vide se ferme après ce délai. "
            "Sinon, un sondage public dure 24 h (Discord le ferme lui-même, entre 1 h et "
            "32 jours) et un sondage anonyme ou classé reste ouvert."
        ),
    )


logger = logutil.init_logger(__name__)
UTILS = ModuleConfig("moduleUtils")

# One /effacer purges this many messages at most.
MAX_PURGE = 1000
# Channels a purge can run in: text, announcements and their threads.
PURGEABLE_CHANNEL_TYPES: list[ChannelType | int] = [
    ChannelType.GUILD_TEXT,
    ChannelType.GUILD_NEWS,
    ChannelType.GUILD_PUBLIC_THREAD,
    ChannelType.GUILD_PRIVATE_THREAD,
    ChannelType.GUILD_NEWS_THREAD,
]


class AdminExtension(Extension):
    MODULE_KEYS = ("moduleUtils",)

    @slash_command(
        name="ping",
        description="Vérifier la latence du bot",
        scopes=guild_scopes(UTILS.guild_ids()),
    )
    @cooldown(Buckets.USER, 3, 30)
    async def ping(self, ctx: SlashContext):
        await ctx.send(f"Pong ! Latence : {round(ctx.bot.latency * 1000)}ms")

    @slash_command(
        name="effacer",
        description="Supprimer des messages",
        scopes=guild_scopes(UTILS.guild_ids()),
    )
    @slash_option(
        "nombre",
        "Nombre de messages à supprimer",
        opt_type=OptionType.INTEGER,
        required=False,
        min_value=1,
        max_value=MAX_PURGE,
    )
    @slash_option(
        "salon",
        "Salon où supprimer les messages",
        opt_type=OptionType.CHANNEL,
        required=False,
        channel_types=PURGEABLE_CHANNEL_TYPES,
        argument_name="channel",
    )
    @slash_option(
        "avant",
        "Supprimer les messages avant le message avec cet ID",
        opt_type=OptionType.STRING,
        required=False,
        argument_name="before",
    )
    @slash_option(
        "apres",
        "Supprimer les messages après le message avec cet ID",
        opt_type=OptionType.STRING,
        required=False,
        argument_name="after",
    )
    @slash_default_member_permission(Permissions.MANAGE_MESSAGES)
    async def delete(
        self,
        ctx: SlashContext,
        nombre=1,
        channel=None,
        before=None,
        after=None,
    ):
        if channel is None:
            channel = ctx.channel
        if not hasattr(channel, "purge"):
            await send_error(ctx, "Impossible de supprimer des messages dans ce salon.")
            return
        if not await author_can(ctx, channel, CAN_PURGE):
            return
        bounds = {}
        for name, value in (("before", before), ("after", after)):
            if value is None:
                continue
            if not str(value).strip().isdigit():
                await send_error(ctx, f"`{name}` doit être un ID de message.")
                return
            bounds[name] = int(value)
        # A purge of hundreds of messages (or of messages over 14 days old,
        # deleted one by one) outlasts the 3 seconds Discord gives to answer.
        await ctx.defer(ephemeral=True)
        deleted = await channel.purge(
            deletion_limit=nombre,
            reason=f"Suppression de {nombre} message(s) par {ctx.user.username} (ID: {ctx.user.id}) via la commande /effacer",
            **bounds,
        )
        await ctx.send(
            f"{deleted} message(s) supprimé(s) dans le channel <#{channel.id}>",
            ephemeral=True,
        )
        logger.info(
            "Suppression de %s message(s) par %s (ID: %s) via la commande /effacer",
            deleted,
            ctx.user.username,
            ctx.user.id,
        )

    @slash_command(
        name="envoyer",
        description="Envoyer un message dans un salon",
        scopes=guild_scopes(UTILS.guild_ids()),
    )
    @slash_option(
        "message",
        "Message à envoyer",
        opt_type=OptionType.STRING,
        required=True,
        max_length=2000,
    )
    @slash_option(
        "salon",
        "Salon où envoyer le message",
        opt_type=OptionType.CHANNEL,
        required=False,
        channel_types=[
            ChannelType.GUILD_TEXT,
            ChannelType.GUILD_NEWS,
        ],
        argument_name="channel",
    )
    @slash_default_member_permission(Permissions.MANAGE_MESSAGES)
    async def send(
        self,
        ctx: SlashContext,
        message: str,
        channel: BaseChannel | None = None,
    ):
        if channel is None:
            channel = ctx.channel
        if channel.type == ChannelType.GUILD_CATEGORY:
            await send_error(ctx, "Je ne peux pas écrire dans une catégorie : choisis un salon.")
            return

        if not hasattr(channel, "send"):
            await send_error(
                ctx, "Ce type de salon ne reçoit pas de messages : choisis un salon textuel."
            )
            return
        if not await author_can(ctx, channel, CAN_SEND):
            return

        text_channel = cast(GuildText, channel)
        sent = await text_channel.send(
            message,
            allowed_mentions=mentions_member_could_send(ctx.author, channel, message),
        )
        logger.info(
            "%s (ID: %s) a envoyé un message dans le channel #%s (ID: %s)",
            ctx.user.username,
            ctx.user.id,
            sent.channel.name,
            sent.channel.id,
        )
        await ctx.send("Message envoyé !", ephemeral=True)

    @slash_command(
        name="mode-lent",
        description="Définir le mode lent d'un salon (0 = désactivé)",
        scopes=guild_scopes(UTILS.guild_ids()),
    )
    @slash_option(
        "secondes",
        "Délai en secondes entre messages (0–21600)",
        opt_type=OptionType.INTEGER,
        required=True,
        min_value=0,
        max_value=21600,
    )
    @slash_option(
        "salon",
        "Salon ciblé (par défaut : salon courant)",
        opt_type=OptionType.CHANNEL,
        required=False,
        channel_types=[
            ChannelType.GUILD_TEXT,
            ChannelType.GUILD_NEWS,
            ChannelType.GUILD_PUBLIC_THREAD,
            ChannelType.GUILD_PRIVATE_THREAD,
            ChannelType.GUILD_NEWS_THREAD,
        ],
        argument_name="channel",
    )
    @slash_default_member_permission(Permissions.MANAGE_CHANNELS)
    async def slowmode(
        self,
        ctx: SlashContext,
        secondes: int,
        channel: BaseChannel | None = None,
    ):
        target = channel or ctx.channel
        if not await author_can(ctx, target, CAN_MANAGE):
            return
        try:
            await target.edit(
                rate_limit_per_user=secondes,
                reason=f"Slowmode défini par {ctx.user.username} (ID: {ctx.user.id})",
            )
        except Exception as e:
            await send_error(ctx, failure_reply("Impossible de modifier le salon", e, logger))
            return

        if secondes == 0:
            await send_success(ctx, f"Mode lent désactivé sur <#{target.id}>.")
        else:
            await send_success(ctx, f"Mode lent réglé à **{secondes}s** sur <#{target.id}>.")
        logger.info(
            "Slowmode set to %ss on #%s by %s (ID: %s)",
            secondes,
            target.name,
            ctx.user.username,
            ctx.user.id,
        )

    @slash_command(
        name="verrouiller",
        description="Verrouiller un salon (empêcher @everyone d'écrire)",
        scopes=guild_scopes(UTILS.guild_ids()),
    )
    @slash_option(
        "salon",
        "Salon à verrouiller (par défaut : salon courant)",
        opt_type=OptionType.CHANNEL,
        required=False,
        channel_types=[ChannelType.GUILD_TEXT, ChannelType.GUILD_NEWS],
        argument_name="channel",
    )
    @slash_option(
        "raison",
        "Raison du verrouillage",
        opt_type=OptionType.STRING,
        required=False,
    )
    @slash_default_member_permission(Permissions.MANAGE_CHANNELS)
    async def lock(
        self,
        ctx: SlashContext,
        channel: BaseChannel | None = None,
        raison: str | None = None,
    ):
        target = channel or ctx.channel
        if not ctx.guild:
            await send_error(ctx, "Cette commande ne peut être utilisée que dans un serveur.")
            return
        if not await author_can(ctx, target, CAN_MANAGE):
            return
        everyone = ctx.guild.default_role
        try:
            await cast(GuildChannel, target).add_permission(
                everyone,
                deny=Permissions.SEND_MESSAGES
                | Permissions.SEND_MESSAGES_IN_THREADS
                | Permissions.CREATE_PUBLIC_THREADS
                | Permissions.CREATE_PRIVATE_THREADS,
                reason=raison or f"Verrouillé par {ctx.user.username} (ID: {ctx.user.id})",
            )
        except Exception as e:
            await send_error(ctx, failure_reply("Impossible de verrouiller le salon", e, logger))
            return

        suffix = f" — {raison}" if raison else ""
        await send_success(ctx, f"🔒 <#{target.id}> verrouillé{suffix}.")
        logger.info(
            "Channel #%s locked by %s (ID: %s)%s",
            target.name,
            ctx.user.username,
            ctx.user.id,
            f" — {raison}" if raison else "",
        )

    @slash_command(
        name="deverrouiller",
        description="Déverrouiller un salon précédemment verrouillé",
        scopes=guild_scopes(UTILS.guild_ids()),
    )
    @slash_option(
        "salon",
        "Salon à déverrouiller (par défaut : salon courant)",
        opt_type=OptionType.CHANNEL,
        required=False,
        channel_types=[ChannelType.GUILD_TEXT, ChannelType.GUILD_NEWS],
        argument_name="channel",
    )
    @slash_default_member_permission(Permissions.MANAGE_CHANNELS)
    async def unlock(
        self,
        ctx: SlashContext,
        channel: BaseChannel | None = None,
    ):
        target = channel or ctx.channel
        if not ctx.guild:
            await send_error(ctx, "Cette commande ne peut être utilisée que dans un serveur.")
            return
        if not await author_can(ctx, target, CAN_MANAGE):
            return
        everyone = ctx.guild.default_role
        try:
            # Clear the deny mask we set in /verrouiller by passing an empty Permissions flag.
            await cast(GuildChannel, target).add_permission(
                everyone,
                deny=Permissions(0),
                reason=f"Déverrouillé par {ctx.user.username} (ID: {ctx.user.id})",
            )
        except Exception as e:
            await send_error(ctx, failure_reply("Impossible de déverrouiller le salon", e, logger))
            return

        await send_success(ctx, f"🔓 <#{target.id}> déverrouillé.")
        logger.info(
            "Channel #%s unlocked by %s (ID: %s)",
            target.name,
            ctx.user.username,
            ctx.user.id,
        )


def setup(bot: Client) -> None:
    AdminExtension(bot)


__all__ = ["AdminExtension", "UtilsConfig", "setup"]
