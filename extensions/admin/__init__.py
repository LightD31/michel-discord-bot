"""Admin Discord extension — general-purpose moderator utilities.

Slash commands:
- ``/ping`` — bot latency probe
- ``/delete`` — bulk-delete messages in a channel
- ``/send`` — send a message to a channel as the bot
- ``/slowmode`` — set or clear a channel's slowmode delay
- ``/lock`` — deny @everyone send permissions on a channel
- ``/unlock`` — restore @everyone send permissions on a channel

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
    GuildText,
    IntegrationType,
    OptionType,
    Permissions,
    SlashContext,
    cooldown,
    slash_command,
    slash_default_member_permission,
    slash_option,
)

from src.core import logging as logutil
from src.core.config import ModuleConfig
from src.discord_ext.channels import member_can
from src.discord_ext.errors import failure_reply
from src.discord_ext.mentions import mentions_member_could_send
from src.discord_ext.messages import send_error, send_success
from src.discord_ext.scopes import guild_scopes
from src.webui.schemas import SchemaBase, enabled_field, register_module, ui


@register_module("moduleUtils")
class UtilsConfig(SchemaBase):
    __label__ = "Utilitaires"
    __description__ = "Commandes utilitaires : ping, sondages, rappels, suppression de messages."
    __icon__ = "🛠️"
    __category__ = "Outils"

    enabled: bool = enabled_field()
    reminderSnoozeMinutes: int = ui(
        "Durée du snooze (rappels)",
        "number",
        default=10,
        description=(
            "Délai en minutes appliqué quand un membre clique sur le bouton "
            "« Snooze » d'un rappel reçu en message privé."
        ),
    )
    pollDefaultDurationMinutes: int = ui(
        "Durée par défaut des sondages",
        "number",
        default=0,
        description=(
            "Si > 0, les commandes /poll, /poll-anonyme et /poll-classement "
            "appliquent cette fermeture automatique quand l'option `duree` "
            "n'est pas fournie."
        ),
    )


logger = logutil.init_logger(__name__)
UTILS = ModuleConfig("moduleUtils")

# One /delete purges this many messages at most.
MAX_PURGE = 1000
# Channels a purge can run in: text, announcements and their threads.
PURGEABLE_CHANNEL_TYPES = [
    ChannelType.GUILD_TEXT,
    ChannelType.GUILD_NEWS,
    ChannelType.GUILD_PUBLIC_THREAD,
    ChannelType.GUILD_PRIVATE_THREAD,
    ChannelType.GUILD_NEWS_THREAD,
]
# What the author must hold in the channel a command acts on.
CAN_PURGE = Permissions.VIEW_CHANNEL | Permissions.MANAGE_MESSAGES
CAN_SEND = Permissions.VIEW_CHANNEL | Permissions.SEND_MESSAGES
CAN_MANAGE = Permissions.VIEW_CHANNEL | Permissions.MANAGE_CHANNELS


async def author_can(ctx: SlashContext, channel: BaseChannel, permission: Permissions) -> bool:
    """Whether the author holds *permission* in *channel*, telling them when they don't.

    Discord checks a command's ``default_member_permissions`` in the channel
    it is run from; the bot then acts on the target with its own rights.
    """
    if member_can(ctx.author, channel, permission):
        return True
    await send_error(ctx, f"Tu n'as pas les droits nécessaires dans <#{channel.id}>.")
    return False


class AdminExtension(Extension):
    MODULE_KEYS = ("moduleUtils",)

    @slash_command(
        name="ping",
        description="Vérifier la latence du bot",
        scopes=guild_scopes(UTILS.guild_ids()),
        integration_types=[IntegrationType.GUILD_INSTALL, IntegrationType.USER_INSTALL],  # type: ignore
    )
    @cooldown(Buckets.USER, 3, 30)
    async def ping(self, ctx: SlashContext):
        await ctx.send(f"Pong ! Latence : {round(ctx.bot.latency * 1000)}ms")

    @slash_command(
        name="delete",
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
        "channel",
        "Channel dans lequel supprimer les messages",
        opt_type=OptionType.CHANNEL,
        required=False,
        channel_types=PURGEABLE_CHANNEL_TYPES,
    )
    @slash_option(
        "before",
        "Supprimer les messages avant le message avec cet ID",
        opt_type=OptionType.STRING,
        required=False,
    )
    @slash_option(
        "after",
        "Supprimer les messages après le message avec cet ID",
        opt_type=OptionType.STRING,
        required=False,
    )
    @slash_default_member_permission(Permissions.ADMINISTRATOR | Permissions.MANAGE_MESSAGES)
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
            reason=f"Suppression de {nombre} message(s) par {ctx.user.username} (ID: {ctx.user.id}) via la commande /delete",
            **bounds,
        )
        await ctx.send(
            f"{deleted} message(s) supprimé(s) dans le channel <#{channel.id}>",
            ephemeral=True,
        )
        logger.info(
            "Suppression de %s message(s) par %s (ID: %s) via la commande /delete",
            deleted,
            ctx.user.username,
            ctx.user.id,
        )

    @slash_command(
        name="send",
        description="Envoyer un message dans un channel",
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
        "channel",
        "Channel dans lequel envoyer le message",
        opt_type=OptionType.CHANNEL,
        required=False,
        channel_types=[
            ChannelType.GUILD_TEXT,
            ChannelType.GUILD_NEWS,
        ],
    )
    @slash_default_member_permission(Permissions.ADMINISTRATOR | Permissions.MANAGE_MESSAGES)
    async def send(
        self,
        ctx: SlashContext,
        message: str,
        channel: BaseChannel | None = None,
    ):
        if channel is None:
            channel = ctx.channel
        if channel.type == ChannelType.GUILD_CATEGORY:
            await send_error(ctx, "Vous ne pouvez pas envoyer de message dans une catégorie")
            return

        if not hasattr(channel, "send"):
            await send_error(ctx, "Ce type de channel ne supporte pas l'envoi de messages")
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
        name="slowmode",
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
        "channel",
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
        name="lock",
        description="Verrouiller un salon (empêcher @everyone d'écrire)",
        scopes=guild_scopes(UTILS.guild_ids()),
    )
    @slash_option(
        "channel",
        "Salon à verrouiller (par défaut : salon courant)",
        opt_type=OptionType.CHANNEL,
        required=False,
        channel_types=[ChannelType.GUILD_TEXT, ChannelType.GUILD_NEWS],
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
            await target.add_permission(
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
        name="unlock",
        description="Déverrouiller un salon précédemment verrouillé",
        scopes=guild_scopes(UTILS.guild_ids()),
    )
    @slash_option(
        "channel",
        "Salon à déverrouiller (par défaut : salon courant)",
        opt_type=OptionType.CHANNEL,
        required=False,
        channel_types=[ChannelType.GUILD_TEXT, ChannelType.GUILD_NEWS],
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
            # Clear the deny mask we set in /lock by passing an empty Permissions flag.
            await target.add_permission(
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
