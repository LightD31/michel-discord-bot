"""Admin Discord extension — general-purpose moderator utilities.

Slash commands:
- ``/ping`` — bot latency probe
- ``/delete`` — bulk-delete messages in a channel
- ``/send`` — send a message to a channel as the bot

Enablement is shared with :mod:`extensions.polls` and
:mod:`extensions.reminders` via the ``moduleUtils`` config key. This package
owns the :class:`UtilsConfig` schema registration.
"""

import os
from typing import cast

from interactions import (
    BaseChannel,
    ChannelType,
    Client,
    Extension,
    GuildText,
    IntegrationType,
    OptionType,
    Permissions,
    SlashContext,
    slash_command,
    slash_default_member_permission,
    slash_option,
)

from src.core import logging as logutil
from src.core.config import load_config
from src.discord_ext.messages import send_error
from src.webui.schemas import SchemaBase, enabled_field, register_module


@register_module("moduleUtils")
class UtilsConfig(SchemaBase):
    __label__ = "Utilitaires"
    __description__ = "Commandes utilitaires : ping, sondages, rappels, suppression de messages."
    __icon__ = "🛠️"
    __category__ = "Outils"

    enabled: bool = enabled_field()


logger = logutil.init_logger(os.path.basename(__file__))
_, _, enabled_servers = load_config("moduleUtils")
enabled_servers_int = [int(s) for s in enabled_servers]  # type: ignore[misc]


class AdminExtension(Extension):
    @slash_command(
        name="ping",
        description="Vérifier la latence du bot",
        scopes=enabled_servers_int,
        integration_types=[IntegrationType.GUILD_INSTALL, IntegrationType.USER_INSTALL],  # type: ignore
    )
    async def ping(self, ctx: SlashContext):
        await ctx.send(f"Pong ! Latence : {round(ctx.bot.latency * 1000)}ms")

    @slash_command(
        name="delete",
        description="Supprimer des messages",
        scopes=enabled_servers_int,  # type: ignore
    )
    @slash_option(
        "nombre",
        "Nombre de messages à supprimer",
        opt_type=OptionType.INTEGER,
        required=False,
        min_value=1,
    )
    @slash_option(
        "channel",
        "Channel dans lequel supprimer les messages",
        opt_type=OptionType.CHANNEL,
        required=False,
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
        await channel.purge(
            deletion_limit=nombre,
            reason=f"Suppression de {nombre} message(s) par {ctx.user.username} (ID: {ctx.user.id}) via la commande /delete",
            before=before,
            after=after,
        )
        await ctx.send(
            f"{nombre} message(s) supprimé(s) dans le channel <#{channel.id}>",
            ephemeral=True,
        )
        logger.info(
            "Suppression de %s message(s) par %s (ID: %s) via la commande /delete",
            nombre,
            ctx.user.username,
            ctx.user.id,
        )

    @slash_command(
        name="send",
        description="Envoyer un message dans un channel",
        scopes=enabled_servers_int,  # type: ignore
    )
    @slash_option(
        "message",
        "Message à envoyer",
        opt_type=OptionType.STRING,
        required=True,
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

        text_channel = cast(GuildText, channel)
        sent = await text_channel.send(message)
        logger.info(
            "%s (ID: %s) a envoyé un message dans le channel #%s (ID: %s)",
            ctx.user.username,
            ctx.user.id,
            sent.channel.name,
            sent.channel.id,
        )
        await ctx.send("Message envoyé !", ephemeral=True)


def setup(bot: Client) -> None:
    AdminExtension(bot)


__all__ = ["AdminExtension", "UtilsConfig", "setup"]
