"""CommandsMixin — /rank and /leaderboard slash commands."""

from datetime import datetime

import pymongo
from interactions import Client, Embed, OptionType, SlashContext, User, slash_command, slash_option

from features.xp import TTLCache, XpRepository, calculate_level, create_progress_bar
from src.discord_ext.messages import send_error
from src.discord_ext.paginator import CustomPaginator

from ._common import EMBED_COLOR, TIMEZONE, enabled_servers, logger


class CommandsMixin:
    bot: Client
    _db_connected: bool
    _rank_cache: TTLCache
    _repos: dict[str, XpRepository]

    def _repo(self, guild_id: str) -> XpRepository: ...  # provided by LevelingMixin

    async def _build_leaderboard_embeds(self, guild) -> list[Embed]:  # from LeaderboardMixin
        ...

    async def _get_user_rank(self, guild_id: str, user_id: str) -> int | None:
        if not self._db_connected:
            return None

        cache_key = f"{guild_id}_{user_id}"
        cached_rank = self._rank_cache.get(cache_key)
        if cached_rank is not None:
            return cached_rank

        try:
            rank = await self._repo(guild_id).get_user_rank(user_id)
        except pymongo.errors.PyMongoError as e:
            logger.error("Failed to get rank for user %s in guild %s: %s", user_id, guild_id, e)
            return None

        if rank is not None:
            self._rank_cache.set(cache_key, rank)
        return rank

    @slash_command(
        name="rank",
        description="Affiche le niveau et l'XP d'un utilisateur",
        scopes=enabled_servers,
    )
    @slash_option(
        "utilisateur",
        "Utilisateur dont afficher le niveau et l'XP",
        opt_type=OptionType.USER,
        required=False,
    )
    async def rank(self, ctx: SlashContext, utilisateur: User = None):
        if not self._db_connected:
            await send_error(ctx, "La base de données n'est pas disponible.")
            return

        target_user = utilisateur or ctx.author
        guild_id = str(ctx.guild.id)

        try:
            stats = await self._repo(guild_id).get_user(str(target_user.id))
        except pymongo.errors.PyMongoError as e:
            logger.error("Database error in rank command: %s", e)
            await send_error(ctx, "Erreur lors de la récupération des statistiques.")
            return

        if stats is None:
            await ctx.send(f"{target_user.mention} n'a pas encore de niveau.")
            return

        xp = stats.get("xp", 0)
        lvl, xp_in_level, xp_max = calculate_level(xp)
        rank = await self._get_user_rank(guild_id, str(target_user.id))
        rank_display = f"{rank}/{ctx.guild.member_count}" if rank else "N/A"

        embed = Embed(
            title=f"Statistiques de {target_user.username}",
            color=EMBED_COLOR,
            timestamp=datetime.now(TIMEZONE),
        )
        embed.add_field(name="Name", value=target_user.mention, inline=True)
        embed.add_field(name="Niveau", value=lvl, inline=True)
        embed.add_field(name="Rank", value=rank_display, inline=True)
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
        name="leaderboard",
        description="Affiche le classement des utilisateurs",
        scopes=enabled_servers,
    )
    async def leaderboard(self, ctx: SlashContext):
        embeds = await self._build_leaderboard_embeds(ctx.guild)
        paginator = CustomPaginator.create_from_embeds(self.bot, *embeds, timeout=3600)
        await paginator.send(ctx)
