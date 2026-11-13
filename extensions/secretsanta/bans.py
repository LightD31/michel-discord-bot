"""Banned-pair subcommands: banpair / unbanpair / listbans."""

import os

from interactions import (
    Embed,
    Member,
    OptionType,
    SlashContext,
    User,
    slash_command,
    slash_option,
)

from src.core import logging as logutil
from src.discord_ext.embeds import Colors
from src.discord_ext.messages import fetch_user_safe, send_error
from src.discord_ext.scopes import guild_scopes

from ._common import ORGANISER_ONLY, SANTA, get_context_id, may_organise

logger = logutil.init_logger(__name__)


class BansMixin:
    """Manage forbidden giver/receiver pairs per context (the organiser only)."""

    async def _organiser_check(self, ctx: SlashContext, context_id: str) -> bool:
        """Answer and return ``False`` unless the author may manage exclusions now.

        A member who could ban pairs would steer the draw: excluding everyone
        but one target leaves the draw a single choice.
        """
        session = await self.repository.get_session(context_id)
        if not may_organise(ctx, session):
            await send_error(ctx, ORGANISER_ONLY)
            return False
        if session is not None and session.is_drawn:
            await send_error(
                ctx,
                "Le tirage a déjà eu lieu : créez une nouvelle session pour changer les exclusions.",
            )
            return False
        return True

    @slash_command(
        name="secretsanta",
        description="Commandes du Père Noël Secret",
        scopes=guild_scopes(SANTA.guild_ids()),
        sub_cmd_name="banpair",
        sub_cmd_description="Interdit deux utilisateurs de se tirer mutuellement",
    )
    @slash_option(
        name="user1",
        description="Premier utilisateur",
        required=True,
        opt_type=OptionType.USER,
    )
    @slash_option(
        name="user2",
        description="Deuxième utilisateur",
        required=True,
        opt_type=OptionType.USER,
    )
    async def ban_pair(self, ctx: SlashContext, user1: Member | User, user2: Member | User) -> None:
        if user1.id == user2.id:
            await send_error(ctx, "Vous ne pouvez pas bannir un utilisateur avec lui-même.")
            return

        context_id = get_context_id(ctx)
        if not await self._organiser_check(ctx, context_id):
            return
        banned_pairs = await self.repository.read_banned_pairs(context_id)

        for p1, p2 in banned_pairs:
            if (user1.id == p1 and user2.id == p2) or (user1.id == p2 and user2.id == p1):
                await send_error(
                    ctx, "Ces utilisateurs sont déjà interdits de se tirer mutuellement."
                )
                return

        banned_pairs.append((user1.id, user2.id))
        await self.repository.write_banned_pairs(context_id, banned_pairs)

        await ctx.send(
            embed=Embed(
                title="🎅 Paire interdite",
                description=f"{user1.mention} et {user2.mention} ne pourront pas se tirer mutuellement.",
                color=Colors.SECRET_SANTA,
            ),
            ephemeral=True,
        )

    @slash_command(
        name="secretsanta",
        description="Commandes du Père Noël Secret",
        scopes=guild_scopes(SANTA.guild_ids()),
        sub_cmd_name="unbanpair",
        sub_cmd_description="Autorise à nouveau deux utilisateurs à se tirer mutuellement",
    )
    @slash_option(
        name="user1",
        description="Premier utilisateur",
        required=True,
        opt_type=OptionType.USER,
    )
    @slash_option(
        name="user2",
        description="Deuxième utilisateur",
        required=True,
        opt_type=OptionType.USER,
    )
    async def unban_pair(
        self, ctx: SlashContext, user1: Member | User, user2: Member | User
    ) -> None:
        context_id = get_context_id(ctx)
        if not await self._organiser_check(ctx, context_id):
            return
        banned_pairs = await self.repository.read_banned_pairs(context_id)

        new_pairs = [
            (p1, p2)
            for p1, p2 in banned_pairs
            if not ((user1.id == p1 and user2.id == p2) or (user1.id == p2 and user2.id == p1))
        ]

        if len(new_pairs) == len(banned_pairs):
            await send_error(
                ctx, "Ces utilisateurs ne sont pas interdits de se tirer mutuellement."
            )
            return

        await self.repository.write_banned_pairs(context_id, new_pairs)

        await ctx.send(
            embed=Embed(
                title="🎅 Paire autorisée",
                description=f"{user1.mention} et {user2.mention} peuvent à nouveau se tirer mutuellement.",
                color=Colors.SECRET_SANTA,
            ),
            ephemeral=True,
        )

    @slash_command(
        name="secretsanta",
        description="Commandes du Père Noël Secret",
        scopes=guild_scopes(SANTA.guild_ids()),
        sub_cmd_name="listbans",
        sub_cmd_description="Liste les paires d'utilisateurs interdites",
    )
    async def list_bans(self, ctx: SlashContext) -> None:
        context_id = get_context_id(ctx)
        if not may_organise(ctx, await self.repository.get_session(context_id)):
            await send_error(ctx, ORGANISER_ONLY)
            return
        banned_pairs = await self.repository.read_banned_pairs(context_id)

        if not banned_pairs:
            await ctx.send(
                embed=Embed(
                    title="🎅 Aucune restriction",
                    description="Aucune paire d'utilisateurs n'est interdite.",
                    color=Colors.SECRET_SANTA,
                ),
                ephemeral=True,
            )
            return

        description = ""
        for user1_id, user2_id in banned_pairs:
            _, user1 = await fetch_user_safe(self.bot, user1_id)
            _, user2 = await fetch_user_safe(self.bot, user2_id)
            user1_mention = user1.mention if user1 else f"<@{user1_id}>"
            user2_mention = user2.mention if user2 else f"<@{user2_id}>"
            description += f"• {user1_mention} ↔ {user2_mention}\n"

        await ctx.send(
            embed=Embed(
                title="🎅 Paires interdites",
                description=description,
                color=Colors.SECRET_SANTA,
            ),
            ephemeral=True,
        )
