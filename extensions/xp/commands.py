"""CommandsMixin — /niveau and /classement slash commands."""

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING

from interactions import (
    Buckets,
    Client,
    Embed,
    File,
    OptionType,
    SlashCommandChoice,
    SlashContext,
    User,
    cooldown,
    slash_command,
    slash_option,
)

from features.xp import (
    TTLCache,
    XpRepository,
    calculate_level,
    create_progress_bar,
    render_rank_card,
)
from features.xp.seasons import Season, season_of
from src.core import clock
from src.core.errors import DatabaseError
from src.discord_ext.avatars import avatar_png
from src.discord_ext.look import card
from src.discord_ext.mentions import NO_PINGS
from src.discord_ext.messages import guild_id_of, guild_of, send_error
from src.discord_ext.paginator import CustomPaginator
from src.discord_ext.scopes import guild_scopes

from ._common import TIMEZONE, XP, logger


class CommandsMixin:
    bot: Client
    _rank_cache: TTLCache

    if TYPE_CHECKING:  # LevelingMixin's

        def _repo(self, guild_id: str) -> XpRepository: ...

    if TYPE_CHECKING:  # LeaderboardMixin, after this mixin in the MRO

        async def _build_leaderboard_embeds(self, guild) -> list[Embed]: ...

        async def _build_season_embeds(self, guild, season: Season) -> list[Embed]: ...

    async def _get_user_rank(self, guild_id: str, user_id: str) -> int | None:
        cache_key = f"{guild_id}_{user_id}"
        cached_rank = self._rank_cache.get(cache_key)
        if cached_rank is not None:
            return cached_rank

        try:
            rank = await self._repo(guild_id).get_user_rank(user_id)
        except DatabaseError as e:
            logger.error("Failed to get rank for user %s in guild %s: %s", user_id, guild_id, e)
            return None

        if rank is not None:
            self._rank_cache.set(cache_key, rank)
        return rank

    @slash_command(
        name="niveau",
        description="Affiche le niveau et l'XP d'un utilisateur",
        scopes=guild_scopes(XP.guild_ids()),
    )
    @slash_option(
        "membre",
        "Utilisateur dont afficher le niveau et l'XP",
        opt_type=OptionType.USER,
        required=False,
        argument_name="utilisateur",
    )
    @cooldown(Buckets.USER, 3, 30)
    async def rank(self, ctx: SlashContext, utilisateur: User | None = None):
        target_user = utilisateur or ctx.author
        guild_id = str(guild_of(ctx).id)

        try:
            stats = await self._repo(guild_id).get_user(str(target_user.id))
        except DatabaseError as e:
            logger.error("Database error in rank command: %s", e)
            await send_error(ctx, "Erreur lors de la récupération des statistiques.")
            return

        if stats is None:
            # A public reply: name the member without notifying them.
            await ctx.send(
                f"{target_user.mention} n'a pas encore de niveau.", allowed_mentions=NO_PINGS
            )
            return

        await ctx.defer()
        xp = stats.get("xp", 0)
        lvl, xp_in_level, xp_max = calculate_level(xp)
        rank = await self._get_user_rank(guild_id, str(target_user.id))

        avatar_bytes = await avatar_png(target_user)
        try:
            buffer = await asyncio.to_thread(  # Pillow work, off the event loop
                render_rank_card,
                avatar_bytes=avatar_bytes,
                username=target_user.username,
                display_name=getattr(target_user, "display_name", target_user.username),
                level=lvl,
                xp_in_level=xp_in_level,
                xp_to_next=xp_max,
                total_xp=xp,
                rank=rank,
                member_count=guild_of(ctx).member_count,
                message_count=stats.get("msg", 0),
            )
            await ctx.send(file=File(file=buffer, file_name="rank.png"))
            return
        except Exception as e:
            logger.warning("Falling back to embed rank card: %s", e)

        # Embed fallback when image rendering fails (e.g. missing fonts/Pillow).
        rank_display = f"{rank}/{guild_of(ctx).member_count}" if rank else "N/A"
        embed = card(
            "moduleXp",
            title=f"Statistiques de {target_user.username}",
            timestamp=datetime.now(TIMEZONE),
        )
        embed.add_field(name="Membre", value=target_user.mention, inline=True)
        embed.add_field(name="Niveau", value=lvl, inline=True)
        embed.add_field(name="Rang", value=rank_display, inline=True)
        embed.add_field(name="XP", value=f"{xp_in_level}/{xp_max}", inline=True)
        embed.add_field(name="Messages", value=stats.get("msg", 0), inline=True)
        embed.add_field(
            name="Progression",
            value=create_progress_bar(xp_in_level, xp_max),
            inline=False,
        )
        embed.set_thumbnail(url=target_user.avatar_url)
        await ctx.send(embed=embed)

    @slash_command(
        name="classement",
        description="Affiche le classement des utilisateurs",
        scopes=guild_scopes(XP.guild_ids()),
    )
    @slash_option(
        "periode",
        "La saison en cours (par défaut quand les saisons sont activées) ou depuis toujours",
        opt_type=OptionType.STRING,
        required=False,
        choices=[
            SlashCommandChoice(name="Saison en cours", value="saison"),
            SlashCommandChoice(name="Depuis toujours", value="toujours"),
        ],
    )
    @cooldown(Buckets.USER, 1, 30)
    async def leaderboard(self, ctx: SlashContext, periode: str | None = None):
        # Members who left are fetched one by one: longer than Discord's 3 s on a big server.
        await ctx.defer()
        settings = XP.get(guild_id_of(ctx))
        if settings.get("seasonsEnabled") and periode != "toujours":
            season = season_of(clock.now(), settings.get("seasonMonths", 1))
            embeds = await self._build_season_embeds(guild_of(ctx), season)
        else:
            embeds = await self._build_leaderboard_embeds(guild_of(ctx))
        paginator = CustomPaginator.create_from_embeds(self.bot, *embeds, timeout=3600)
        await paginator.send(ctx)
