"""LeaderboardMixin — permanent leaderboard updater task and embed builders."""

import traceback
from datetime import datetime

import pymongo
from interactions import Client, Embed, Guild, Message, Task, TimeTrigger

from features.xp import (
    LEADERBOARD_PAGE_SIZE,
    TTLCache,
    XpRepository,
    calculate_level,
    get_rank_display,
)
from src.core.text import format_number
from src.discord_ext.embeds import Colors
from src.discord_ext.messages import fetch_or_create_persistent_message, fetch_user_safe
from src.discord_ext.paginator import CustomPaginator

from ._common import EMBED_COLOR, TIMEZONE, enabled_servers, logger, module_config


def create_leaderboard_embed(guild_name: str, is_continuation: bool = False) -> Embed:
    title = f"Classement de {guild_name}"
    if is_continuation:
        title += " (cont.)"
    return Embed(title=title, color=EMBED_COLOR, timestamp=datetime.now(TIMEZONE))


class LeaderboardMixin:
    bot: Client
    _db_connected: bool
    _user_cache: TTLCache
    _repos: dict[str, XpRepository]

    def _repo(self, guild_id: str) -> XpRepository: ...  # provided by LevelingMixin

    async def _get_member_info(self, guild: Guild, user_id: str) -> tuple[str, str] | None:
        cache_key = f"user_{user_id}"
        cached_info = self._user_cache.get(cache_key)
        if cached_info is not None:
            return cached_info

        try:
            member = guild.get_member(user_id)
            if member is None:
                _, member = await fetch_user_safe(self.bot, user_id)
            if member is None:
                return None

            result = (member.display_name, member.username)
            self._user_cache.set(cache_key, result)
            return result
        except Exception as e:
            logger.debug("Could not fetch member %s: %s", user_id, e)
            return None

    @staticmethod
    def _format_leaderboard_entry(
        rank: int,
        display_name: str,
        username: str,
        lvl: int,
        xp_in_level: int,
        xp_max: int,
        total_xp: int,
        msg_count: int,
    ) -> tuple[str, str]:
        rank_text = get_rank_display(rank)
        percentage = f"({(xp_in_level / xp_max * 100):.0f}"
        name = f"{rank_text} {display_name} ({username})"
        value = (
            f"`Niveau {str(lvl).rjust(2)} {percentage.rjust(3)} %) | "
            f"XP : {str(format_number(total_xp)).rjust(7)} | "
            f"{str(format_number(msg_count)).rjust(5)} messages`"
        )
        return name, value

    async def _build_leaderboard_embeds(self, guild: Guild) -> list[Embed]:
        if not self._db_connected:
            return [
                Embed(
                    title="Erreur",
                    description="La base de données n'est pas disponible.",
                    color=Colors.ERROR,
                )
            ]

        try:
            rankings = await self._repo(str(guild.id)).list_all_sorted_by_xp()
        except pymongo.errors.PyMongoError as e:
            logger.error("Database error building leaderboard: %s", e)
            return [
                Embed(
                    title="Erreur",
                    description="Impossible de récupérer le classement.",
                    color=Colors.ERROR,
                )
            ]

        embeds = []
        embed = create_leaderboard_embed(guild.name)
        entry_count = 0

        for entry in rankings:
            try:
                member_info = await self._get_member_info(guild, entry["_id"])
                if member_info is None:
                    continue

                display_name, username = member_info
                total_xp = entry.get("xp", 0)
                lvl, xp_in_level, xp_max = calculate_level(total_xp)

                name, value = self._format_leaderboard_entry(
                    entry_count,
                    display_name,
                    username,
                    lvl,
                    xp_in_level,
                    xp_max,
                    total_xp,
                    entry.get("msg", 0),
                )
                embed.add_field(name=name, value=value, inline=False)
                entry_count += 1

                if entry_count % LEADERBOARD_PAGE_SIZE == 0:
                    embeds.append(embed)
                    embed = create_leaderboard_embed(guild.name, is_continuation=True)
            except KeyError:
                continue

        embeds.append(embed)
        return embeds

    @Task.create(TimeTrigger(utc=False))
    async def leaderboardpermanent(self):
        for guild_id in enabled_servers:
            await self._update_permanent_leaderboard(guild_id)

    async def _update_permanent_leaderboard(self, guild_id: str) -> None:
        guild_config = module_config.get(guild_id, {})
        channel_id = guild_config.get("xpChannelId")
        if not channel_id:
            logger.debug("No leaderboard channel configured for %s", guild_id)
            return

        try:
            guild: Guild = await self.bot.fetch_guild(guild_id)
            if guild is None:
                logger.error("Could not fetch guild %s", guild_id)
                return

            message: Message = await fetch_or_create_persistent_message(
                self.bot,
                channel_id=channel_id,
                message_id=guild_config.get("xpMessageId"),
                module_name="moduleXp",
                message_id_key="xpMessageId",
                guild_id=guild_id,
                initial_content="Initialisation du leaderboard…",
                pin=bool(guild_config.get("xpPinMessage", False)),
                logger=logger,
            )
            if message is None:
                logger.warning("Could not get or create leaderboard message for %s", guild_id)
                return
            guild_config["xpMessageId"] = str(message.id)

            embeds = await self._build_leaderboard_embeds(guild)
            paginator = CustomPaginator.create_from_embeds(self.bot, *embeds)

            logger.debug("Updating leaderboard for %s", guild.name)
            paginator_dict = paginator.to_dict()
            await message.edit(
                content="",
                embeds=paginator_dict["embeds"],
                components=paginator_dict["components"],
            )
        except Exception as e:
            logger.error(
                "Failed to update leaderboard for guild %s: %s\n%s",
                guild_id,
                e,
                traceback.format_exc(),
            )
