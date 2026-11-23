"""SeasonsMixin — the end of an XP season: its podium, its champion role (A4).

Every night at 00:07 (Paris), each guild with ``seasonsEnabled`` looks at the
season that just ended. Its top 10 is recorded first (``record_season``, once:
a second run, or a bot restarted mid-way, finds it recorded and stops), then
the podium is posted and the champion role moves. A season that ended more
than a week ago is left alone: switching seasons on doesn't post the podium of
a season nobody knew about.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from interactions import Client, Embed, Task, TimeTrigger

from features.xp.seasons import KEPT_PER_SEASON, Season, podium_lines, previous_season
from src.core import clock, log_context
from src.core.errors import DatabaseError
from src.core.logging import log_failure
from src.discord_ext.channels import fetch_guild_channel
from src.discord_ext.look import card
from src.discord_ext.mentions import NO_PINGS
from src.discord_ext.role_grants import grantable_role

from ._common import XP, logger

if TYPE_CHECKING:
    from features.xp import XpRepository

# How late a season's end may still be announced (the bot was down meanwhile).
CLOSING_GRACE = timedelta(days=7)


def podium_embed(season: Season, ranking: list[tuple[str, int]], names: dict[str, str]) -> Embed:
    embed = card(
        "moduleXp",
        title=f"🏆 Fin de la saison : {season.label}",
        description="\n".join(podium_lines(ranking, names)),
        footer="Une nouvelle saison commence : tout le monde repart à zéro !",
    )
    rest = ranking[3:KEPT_PER_SEASON]
    if rest:
        embed.add_field(
            name="Et aussi",
            value="\n".join(
                f"{i}. {names.get(uid, f'<@{uid}>')}" for i, (uid, _) in enumerate(rest, start=4)
            ),
            inline=False,
        )
    return embed


class SeasonsMixin:
    bot: Client

    if TYPE_CHECKING:

        def _repo(self, guild_id: str) -> XpRepository: ...

    @Task.create(TimeTrigger(hour=0, minute=7, utc=False))
    async def _close_seasons(self) -> None:
        now = clock.now()
        for guild_id, settings in XP.items():
            with log_context.scope(guild_id=guild_id):
                if not settings.get("seasonsEnabled"):
                    continue
                try:
                    await self.close_season(guild_id, settings, now)
                except Exception as e:
                    log_failure(logger, "Season of guild %s not closed: %s", guild_id, e)

    async def close_season(self, guild_id: str, settings: dict, now: Any) -> bool:
        """Close the season that just ended in *guild_id*; ``True`` when this run did it."""
        season = previous_season(now, settings.get("seasonMonths", 1))
        if now - season.end > CLOSING_GRACE:
            return False
        repo = self._repo(guild_id)
        if await repo.get_season(season.key) is not None:
            return False
        ranking = await repo.season_ranking(season.start, season.end, KEPT_PER_SEASON)
        try:
            if not await repo.record_season(season.key, season.label, ranking, now):
                return False
        except DatabaseError as e:
            logger.warning("Season %s of guild %s not recorded: %s", season.key, guild_id, e)
            return False
        logger.info("Season %s closed in guild %s (%d ranked)", season.key, guild_id, len(ranking))
        if not ranking:
            return True
        guild = self.bot.get_guild(int(guild_id))
        names = {}
        for uid, _ in ranking:
            member = guild.get_member(int(uid)) if guild else None
            if member is not None:
                names[uid] = str(member.display_name)
        channel_id = settings.get("seasonChannelId") or settings.get("xpChannelId")
        if channel_id:
            channel = await fetch_guild_channel(self.bot, guild_id, channel_id, logger=logger)
            if channel is not None and hasattr(channel, "send"):
                try:
                    await channel.send(
                        embeds=[podium_embed(season, ranking, names)],
                        allowed_mentions=NO_PINGS,
                    )
                except Exception as e:
                    logger.warning("Podium of guild %s not posted: %s", guild_id, e)
        if guild is not None and settings.get("seasonChampionRoleId"):
            await self._crown(guild, str(settings["seasonChampionRoleId"]), ranking[0][0], season)
        return True

    async def _crown(self, guild: Any, role_id: str, winner_id: str, season: Season) -> None:
        """Move the champion role to *winner_id* (re-checked: a role can change after the save)."""
        role, refusal = grantable_role(guild, role_id)
        if role is None:
            logger.warning(
                "Guild %s: champion role %s not granted (%s)", guild.id, role_id, refusal
            )
            return
        reason = f"Champion·ne de la saison {season.label}"
        for member in list(getattr(guild, "members", None) or []):
            if str(member.id) != winner_id and member.has_role(role):
                try:
                    await member.remove_role(role, reason=reason)
                except Exception as e:
                    logger.debug("Champion role not removed from %s: %s", member.id, e)
        winner = guild.get_member(int(winner_id))
        if winner is None:
            return
        try:
            await winner.add_role(role, reason=reason)
        except Exception as e:
            logger.warning("Champion role not granted to %s: %s", winner_id, e)


__all__ = ["SeasonsMixin", "podium_embed"]
