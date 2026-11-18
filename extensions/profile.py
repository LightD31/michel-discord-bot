"""``/profil`` and the "📊 Profil" right-click menu: a member's card (P5, A2).

The rank card of ``/niveau`` (level, rank, XP), with what the member left
across the server underneath: the XP of the current season, messages on the
starboard and the stars they gathered, tracks added to the playlist,
suggestions approved, giveaways won, and the day of their birthday (never the
year). Each line shows only where its module is on and there is something to
say; all of it is already public on the server. Lives where XP is on.
"""

from __future__ import annotations

import asyncio
from typing import Any

from interactions import (
    Buckets,
    Client,
    ContextMenuContext,
    Embed,
    Extension,
    File,
    OptionType,
    SlashContext,
    User,
    cooldown,
    slash_command,
    slash_option,
    user_context_menu,
)

from features.profile import Footprint, ProfileRepository
from features.secretsanta.dates import MONTH_NAMES
from features.xp import XpRepository, calculate_level, render_rank_card
from features.xp.seasons import season_of, thousands
from src.core import clock
from src.core import logging as logutil
from src.core.config import ModuleConfig
from src.core.errors import DatabaseError
from src.discord_ext.look import card
from src.discord_ext.mentions import NO_PINGS
from src.discord_ext.menus import menu_help
from src.discord_ext.messages import guild_id_of, send_error
from src.discord_ext.scopes import guild_scopes

logger = logutil.init_logger(__name__)

XP = ModuleConfig("moduleXp")
# Each line of the card, by the module it needs (accessor names kept plain: see CLAUDE.md).
SECTION_MODULES = {
    "stars": ModuleConfig("moduleStarboard"),
    "tracks": ModuleConfig("moduleSpotify"),
    "suggestions": ModuleConfig("moduleSuggestions"),
    "giveaways": ModuleConfig("moduleGiveaway"),
    "birthday": ModuleConfig("moduleBirthday"),
}
PROFILE_MENU = "📊 Profil"


def plural(count: int, word: str) -> str:
    return f"{thousands(count)} {word}{'s' if count > 1 else ''}"


def footprint_lines(found: Footprint, season_label: str | None, season_xp: int | None) -> list[str]:
    """The card's lines, each only when its module is on and there is something to show."""
    lines = []
    if season_label is not None and season_xp is not None:
        lines.append(f"🏁 Saison {season_label} : **{thousands(season_xp)} XP**")
    if found.stars:
        received = found.stars_received or 0
        lines.append(f"⭐ {plural(found.stars, 'message')} au starboard ({thousands(received)} ⭐)")
    if found.tracks:
        lines.append(f"🎵 {plural(found.tracks, 'titre')} ajouté{'s' if found.tracks > 1 else ''}")
    if found.suggestions_done:
        n = found.suggestions_done
        lines.append(f"💡 {plural(n, 'suggestion')} retenue{'s' if n > 1 else ''}")
    if found.giveaways_won:
        n = found.giveaways_won
        lines.append(f"🎁 {plural(n, 'giveaway')} gagné{'s' if n > 1 else ''}")
    if found.birthday is not None:
        day = found.birthday
        first = "1er" if day.day == 1 else str(day.day)
        lines.append(f"🎂 Anniversaire le {first} {MONTH_NAMES[day.month - 1]}")
    return lines


async def _avatar_bytes(user: Any) -> bytes | None:
    asset = getattr(user, "display_avatar", None) or getattr(user, "avatar", None)
    if asset is None:
        return None
    try:
        return await asset.fetch(extension=".png", size=256)
    except Exception as e:
        logger.debug("No avatar for the profile card: %s", e)
        return None


class ProfileExtension(Extension):
    MODULE_KEYS = (
        "moduleXp",
        "moduleStarboard",
        "moduleSpotify",
        "moduleSuggestions",
        "moduleGiveaway",
        "moduleBirthday",
    )

    def __init__(self, bot: Client):
        self.bot = bot

    def xp_repo(self, guild_id: str) -> XpRepository:
        return XpRepository(guild_id)

    def profile_repo(self, guild_id: str) -> ProfileRepository:
        return ProfileRepository(guild_id)

    @slash_command(
        name="profil",
        description="La carte d'un membre : niveau, saison, starboard, playlist, suggestions…",
        scopes=guild_scopes(XP.guild_ids()),
    )
    @slash_option(
        "membre",
        "Le membre à montrer (toi par défaut)",
        opt_type=OptionType.USER,
        required=False,
        argument_name="member",
    )
    @cooldown(Buckets.USER, 3, 30)
    async def profil(self, ctx: SlashContext, member: User | None = None) -> None:
        await self.show_profile(ctx, member or ctx.author, ephemeral=False)

    @menu_help("La carte de ce membre : niveau, saison, starboard, playlist…")
    @user_context_menu(name=PROFILE_MENU, scopes=guild_scopes(XP.guild_ids()))
    async def profile_menu(self, ctx: ContextMenuContext) -> None:
        await self.show_profile(ctx, ctx.target, ephemeral=True)

    async def show_profile(self, ctx: Any, member: Any, *, ephemeral: bool) -> None:
        guild_id = guild_id_of(ctx)
        if getattr(member, "bot", False):
            await send_error(ctx, "Les bots n'ont pas de profil.", icon="🤖")
            return
        await ctx.defer(ephemeral=ephemeral)
        uid = str(member.id)
        sections = {name for name, module in SECTION_MODULES.items() if module.enabled(guild_id)}
        settings = XP.get(guild_id)
        try:
            stats = await self.xp_repo(guild_id).get_user(uid)
            rank = await self.xp_repo(guild_id).get_user_rank(uid) if stats else None
            found = await self.profile_repo(guild_id).footprint(uid, sections)
            season_label = season_xp = None
            if settings.get("seasonsEnabled"):
                season = season_of(clock.now(), settings.get("seasonMonths", 1))
                season_label = season.label
                season_xp = await self.xp_repo(guild_id).season_xp(uid, season.start, season.end)
        except DatabaseError as e:
            logger.warning("Profile of %s in guild %s not read: %s", uid, guild_id, e)
            await send_error(ctx, "Je n'arrive pas à lire les données pour l'instant : réessaie.")
            return

        name = str(getattr(member, "display_name", None) or getattr(member, "username", uid))
        embed = card("moduleXp", title=f"Profil de {name}")
        lines = footprint_lines(found, season_label, season_xp)
        if stats is None:
            lines.insert(0, "Pas encore d'XP sur ce serveur.")
        embed.description = "\n".join(lines) or "Rien à montrer pour l'instant."
        files = []
        if stats is not None:
            xp = int(stats.get("xp", 0))
            lvl, xp_in_level, xp_max = calculate_level(xp)
            try:
                image = await asyncio.to_thread(  # Pillow work, off the event loop
                    render_rank_card,
                    avatar_bytes=await _avatar_bytes(member),
                    username=str(getattr(member, "username", name)),
                    display_name=name,
                    level=lvl,
                    xp_in_level=xp_in_level,
                    xp_to_next=xp_max,
                    total_xp=xp,
                    rank=rank,
                    member_count=getattr(ctx.guild, "member_count", None),
                    message_count=int(stats.get("msg", 0)),
                )
                files.append(File(file=image, file_name="profil.png"))
                embed.set_image(url="attachment://profil.png")
            except Exception as e:
                logger.warning("Profile card not drawn, text only: %s", e)
                rank_text = f" · {rank}ᵉ" if rank else ""
                embed.description = f"Niveau {lvl}{rank_text} · {thousands(xp)} XP\n" + (
                    embed.description or ""
                )
        await ctx.send(embeds=[embed], files=files, ephemeral=ephemeral, allowed_mentions=NO_PINGS)


def setup(bot: Client) -> None:
    ProfileExtension(bot)


__all__ = ["ProfileExtension", "footprint_lines", "setup"]
