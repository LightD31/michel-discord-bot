"""CommandsMixin — general utility slash commands (ping, delete, send)."""

from interactions import (
    BaseChannel,
    ChannelType,
    IntegrationType,
    OptionType,
    Permissions,
    SlashContext,
    slash_command,
    slash_default_member_permission,
    slash_option,
)

from src.discord_ext.messages import send_error

from ._common import enabled_servers_int, logger


class CommandsMixin:
    @slash_command(
        name="ping",
        description="Vérifier la latence du bot",
        scopes=enabled_servers_int,
        integration_types=[IntegrationType.GUILD_INSTALL, IntegrationType.USER_INSTALL],  # type: ignore
    )
    async def ping(self, ctx: SlashContext):
        """
        A slash command that checks the latency of the bot.

        Parameters:
        -----------
        ctx : SlashContext
            The context of the slash command.
        """
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
        """
        A slash command that deletes messages in a channel.

        Parameters:
        -----------
        ctx : SlashContext
            The context of the slash command.
        nombre : int, optional
            The number of messages to delete. Default is 1.
        channel : discord.TextChannel, optional
            The channel in which to delete messages. Default is the current channel.
        before : int, optional
            Delete messages before this message ID.
        after : int, optional
            Delete messages after this message ID.
        """
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
        """
        A slash command that sends a message to a channel.

        Parameters:
        -----------
        ctx : SlashContext
            The context of the slash command.
        message : str
            The message to send.
        """
        if channel is None:
            channel = ctx.channel
        # Check if the channel is a category
        if channel.type == ChannelType.GUILD_CATEGORY:
            await send_error(ctx, "Vous ne pouvez pas envoyer de message dans une catégorie")
            return

        # Ensure channel is a text channel that can send messages
        if not hasattr(channel, "send"):
            await send_error(ctx, "Ce type de channel ne supporte pas l'envoi de messages")
            return

        # Type cast to ensure the channel has the send method
        from typing import cast

        from interactions import GuildText

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
