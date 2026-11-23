"""Succès: badges won across modules, ``/succes``, and their announcement (P18, A3).

Every hour the task looks at each server where the succès are on and awards
the badges (:mod:`features.achievements`) to whoever qualifies: a message on
the starboard, 10 h of voice, 10 tracks in the playlist, a suggestion
implemented, a year on the server, 100 feur. A badge is checked only where
its module is on and the admins left it on. Nothing slows the modules' own
listeners: the badges read what they already store.

New badges are announced in one message per run, in the channel the admins
chose (none: no announcement), with the members named but not pinged. The
first check of a badge on a server awards in silence, so switching the
module on doesn't announce the whole server.

The badges show in ``/succes``, on ``/profil``, in the member space and next
to the names of the XP leaderboard (the dashboard and the public page).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from interactions import (
    Buckets,
    Client,
    Extension,
    IntervalTrigger,
    OptionType,
    SlashContext,
    Task,
    User,
    cooldown,
    listen,
    slash_command,
    slash_option,
)

from features.achievements import (
    BADGES,
    AchievementsRepository,
    Badge,
    active_badges,
    check_badges,
)
from features.achievements.badges import SENIORITY
from src.core import clock, log_context
from src.core import logging as logutil
from src.core.config import ModuleConfig
from src.core.errors import DatabaseError
from src.core.logging import log_failure
from src.discord_ext.channels import fetch_guild_channel
from src.discord_ext.look import card
from src.discord_ext.mentions import NO_PINGS
from src.discord_ext.messages import guild_id_of, send_error
from src.discord_ext.scopes import guild_scopes
from src.webui.schemas import ModuleHelp, SchemaBase, enabled_field, register_module, ui

logger = logutil.init_logger(__name__)

ANNOUNCED_AT_MOST = 15  # lines in one announcement; the rest is counted


def _switch(badge: Badge) -> Any:
    return ui(
        badge.label,
        "boolean",
        default=True,
        description=f"Se débloque avec {badge.goal}.",
    )


@register_module("moduleAchievements")
class AchievementsConfig(SchemaBase):
    __label__ = "Succès"
    __description__ = (
        "Des badges qui se débloquent en participant : starboard, vocal, playlist, "
        "suggestions, ancienneté, feur."
    )
    __icon__ = "🏆"
    __category__ = "Communauté"
    __help__ = ModuleHelp(
        "Des badges à débloquer en participant à la vie du serveur.",
        ("/succes",),
        "Un message au starboard, 10 h de vocal, 10 titres dans la playlist, une suggestion "
        "mise en place, un an sur le serveur ou 100 feur : chaque exploit débloque un badge, "
        "visible sur /profil et dans ton espace du dashboard.",
    )

    enabled: bool = enabled_field()
    achievementsChannelId: str = ui(
        "Salon des annonces",
        "channel",
        required=False,
        description=(
            "Où annoncer les nouveaux badges, un message par heure au plus, sans mentionner "
            "personne. Vide : pas d'annonce. À l'activation, les membres qui ont déjà fait "
            "l'exploit reçoivent leur badge sans annonce."
        ),
    )
    badgeStar: bool = _switch(BADGES[0])
    badgeVoice: bool = _switch(BADGES[1])
    badgeDj: bool = _switch(BADGES[2])
    badgeIdeas: bool = _switch(BADGES[3])
    badgeSeniority: bool = _switch(BADGES[4])
    badgeFeur: bool = _switch(BADGES[5])


ACHIEVEMENTS = ModuleConfig("moduleAchievements")


def seniors_of(guild: Any, now: datetime) -> set[str]:
    """The members of *guild* (bots aside) who joined at least a year ago."""
    found = set()
    for member in list(getattr(guild, "members", None) or []):
        joined = getattr(member, "joined_at", None)
        if joined is None or getattr(member, "bot", False):
            continue
        if clock.as_utc(joined) <= now - SENIORITY:
            found.add(str(member.id))
    return found


def announcement_lines(news: list[tuple[str, Badge]]) -> list[str]:
    lines = [f"<@{uid}> débloque {badge.emoji} **{badge.name}**" for uid, badge in news]
    if len(lines) > ANNOUNCED_AT_MOST:
        rest = len(lines) - ANNOUNCED_AT_MOST
        lines = lines[:ANNOUNCED_AT_MOST] + [f"… et {rest} autre{'s' if rest > 1 else ''}."]
    return lines


def badges_text(won: dict[str, datetime], badges: list[Badge]) -> tuple[list[str], list[str]]:
    """The lines of the badges won (with their day) and of those still to win."""
    done, todo = [], []
    for badge in badges:
        if badge.key in won:
            day = clock.as_utc(won[badge.key]).astimezone(clock.LOCAL_TZ)
            done.append(f"{badge.label} — le {day:%d/%m/%Y}")
        else:
            todo.append(f"{badge.emoji} {badge.name} : {badge.goal}")
    return done, todo


class AchievementsExtension(Extension):
    # The badges read what these modules keep, only where they are on.
    MODULE_KEYS = (
        "moduleAchievements",
        "moduleStarboard",
        "moduleXp",
        "moduleSpotify",
        "moduleSuggestions",
        "moduleFeur",
    )
    HELP_LABEL = "🏆 Succès"

    def __init__(self, bot: Client):
        self.bot = bot

    def repository(self, guild_id: str) -> AchievementsRepository:
        return AchievementsRepository(guild_id)

    @listen()
    async def on_startup(self) -> None:
        for guild_id in ACHIEVEMENTS.guild_ids():
            await self.repository(guild_id).ensure_indexes()  # logs a failure, never raises
        if not self.check_all.running:
            self.check_all.start()

    @Task.create(IntervalTrigger(hours=1))
    async def check_all(self) -> None:
        for guild_id, settings in ACHIEVEMENTS.items():
            # Bound so a failure shows in this server's incidents (src.core.incidents).
            with log_context.scope(guild_id=guild_id):
                try:
                    await self.check_guild(guild_id, settings)
                except Exception as e:
                    log_failure(logger, "Badges of guild %s not checked: %s", guild_id, e)

    async def check_guild(self, guild_id: str, settings: dict) -> list[tuple[str, Badge]]:
        """Award *guild_id*'s badges and announce the new ones; returns them."""
        now = clock.now()
        badges = active_badges(guild_id)
        if not badges:
            return []
        guild = self.bot.get_guild(int(guild_id)) if str(guild_id).isdigit() else None
        news = await check_badges(self.repository(guild_id), badges, seniors_of(guild, now), now)
        if news:
            logger.info("%d new badge(s) in guild %s", len(news), guild_id)
            await self.announce(guild_id, settings, news)
        return news

    async def announce(self, guild_id: str, settings: dict, news: list[tuple[str, Badge]]) -> None:
        channel_id = str(settings.get("achievementsChannelId") or "")
        if not channel_id:
            return
        channel = await fetch_guild_channel(self.bot, guild_id, channel_id, logger=logger)
        if channel is None or not hasattr(channel, "send"):
            logger.warning("Badges of guild %s: the announcement channel is unusable", guild_id)
            return
        embed = card(
            "moduleAchievements",
            "success",
            title="🏆 Nouveaux succès",
            description="\n".join(announcement_lines(news)),
            footer="/succes pour voir les tiens",
        )
        await channel.send(embeds=[embed], allowed_mentions=NO_PINGS)

    @slash_command(
        name="succes",
        description="Les badges d'un membre, et ceux qu'il lui reste à débloquer",
        scopes=guild_scopes(ACHIEVEMENTS.guild_ids()),
    )
    @slash_option(
        "membre",
        "Le membre à montrer (toi par défaut)",
        opt_type=OptionType.USER,
        required=False,
        argument_name="member",
    )
    @cooldown(Buckets.USER, 3, 30)
    async def succes(self, ctx: SlashContext, member: User | None = None) -> None:
        guild_id = guild_id_of(ctx)
        target = member or ctx.author
        if getattr(target, "bot", False):
            await send_error(ctx, "Les bots ne débloquent pas de badges.", icon="🤖")
            return
        badges = active_badges(guild_id)
        if not badges:
            await send_error(
                ctx,
                "Aucun badge n'est actif ici : un administrateur peut les choisir dans le "
                "module Succès du dashboard.",
            )
            return
        try:
            won = await self.repository(guild_id).badges_of(str(target.id))
        except DatabaseError as e:
            logger.warning("Badges of %s in guild %s not read: %s", target.id, guild_id, e)
            await send_error(ctx, "Je n'arrive pas à lire les badges pour l'instant : réessaie.")
            return
        done, todo = badges_text(won, badges)
        mine = target.id == ctx.author.id
        name = str(getattr(target, "display_name", None) or getattr(target, "username", ""))
        embed = card(
            "moduleAchievements",
            title=f"Succès de {name}",
            description=f"**{len(done)}** badge{'s' if len(done) > 1 else ''} sur {len(badges)}.",
            footer="vérifiés toutes les heures",
        )
        if done:
            embed.add_field(name="Débloqués", value="\n".join(done), inline=False)
        if todo:
            title = "À débloquer" if mine else "Encore à débloquer"
            embed.add_field(name=title, value="\n".join(todo), inline=False)
        await ctx.send(embeds=[embed], ephemeral=mine, allowed_mentions=NO_PINGS)


def setup(bot: Client) -> None:
    AchievementsExtension(bot)


__all__ = [
    "ACHIEVEMENTS",
    "AchievementsConfig",
    "AchievementsExtension",
    "announcement_lines",
    "badges_text",
    "seniors_of",
    "setup",
]
