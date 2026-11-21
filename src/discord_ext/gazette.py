"""The Gazette, gathered and posted: shared by the weekly task, ``/gazette`` and the dashboard.

Runs on the bot loop (it reads the guild's members). Each section is read only
when its module is on in the guild: XP for the most active members, the
starboard, birthdays, Spotify, suggestions, giveaways; the newcomers come from
Discord (``joined_at``). Members are named, never mentioned, and the post
pings nobody (``NO_PINGS``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from interactions import Embed

from features.gazette import Edition, GazetteRepository, render, upcoming_birthdays
from features.gazette.edition import TOP_MEMBERS, WEEK
from features.xp import XpRepository
from src.core import clock
from src.core.config import ModuleConfig
from src.discord_ext.look import card
from src.discord_ext.mentions import NO_PINGS
from src.discord_ext.messages import jump_url

SOURCES = {
    "xp": ModuleConfig("moduleXp"),
    "stars": ModuleConfig("moduleStarboard"),
    "birthdays": ModuleConfig("moduleBirthday"),
    "tracks": ModuleConfig("moduleSpotify"),
    "suggestions": ModuleConfig("moduleSuggestions"),
    "giveaways": ModuleConfig("moduleGiveaway"),
}


def _name(guild: Any, user_id: Any) -> str | None:
    """The member's name on the server, ``None`` once they left (or for nobody)."""
    try:
        member = guild.get_member(int(user_id)) if guild is not None else None
    except (TypeError, ValueError):
        return None
    if member is None:
        return None
    return str(getattr(member, "display_name", None) or getattr(member, "username", user_id))


async def gather_edition(bot: Any, guild_id: str, now: datetime) -> Edition:
    """The week before *now* in *guild_id*, its members named."""
    guild = bot.get_guild(int(guild_id))
    start, end = now - WEEK, now
    on = {name for name, module in SOURCES.items() if module.enabled(guild_id)}
    repo = GazetteRepository(guild_id)
    edition = Edition(str(getattr(guild, "name", guild_id)), start, end)

    if "xp" in on:
        ranking = await XpRepository(guild_id).season_ranking(start, end, TOP_MEMBERS * 3)
        named = [(_name(guild, uid), xp) for uid, xp in ranking]
        edition.top_members = [(n, xp) for n, xp in named if n][:TOP_MEMBERS]
    if "stars" in on:
        star = await repo.best_star(start, end)
        if star:
            author = _name(guild, star.get("author_id")) or "un ancien membre"
            link = jump_url(guild_id, star["channel_id"], star["original_message_id"])
            edition.star = (author, int(star.get("count", 0)), link)
    if "birthdays" in on:
        named_days = [
            (n, born) for uid, born in await repo.birth_dates() if (n := _name(guild, uid))
        ]
        edition.birthdays = upcoming_birthdays(named_days, clock.to_local(now).date())
    if "tracks" in on:
        counts = await repo.tracks_added(start, end)
        edition.tracks_added = sum(counts.values())
        for uid, count in counts.most_common():
            name = _name(guild, uid)
            if name:
                edition.top_dj = (name, count)
                break
    if "suggestions" in on:
        edition.suggestions = [
            (int(d.get("id", 0)), str(d.get("status")), str(d.get("title") or d.get("text", "")))
            for d in await repo.decided_suggestions(start, end)
        ]
    if "giveaways" in on:
        edition.giveaways = [
            (
                str(d.get("prize", "")),
                [n for uid in d.get("winners", []) if (n := _name(guild, uid))],
            )
            for d in await repo.drawn_giveaways(start, end)
        ]
    for member in list(getattr(guild, "members", None) or []):
        joined = getattr(member, "joined_at", None)
        if joined is None or getattr(member, "bot", False):
            continue
        joined = clock.as_utc(joined)
        if start <= joined < end:
            edition.newcomers.append(str(member.display_name))
    return edition


def gazette_embed(edition: Edition) -> Embed:
    title, intro, fields = render(edition)
    embed = card("moduleGazette", title=title, description=intro, footer="chaque semaine")
    for name, value in fields:
        embed.add_field(name=name, value=value, inline=False)
    return embed


async def post_edition(channel: Any, edition: Edition) -> Any:
    """Post *edition* in *channel*; returns the message (raises what Discord raised)."""
    return await channel.send(embeds=[gazette_embed(edition)], allowed_mentions=NO_PINGS)


def edition_json(edition: Edition) -> dict[str, Any]:
    """The edition as the dashboard's preview shows it."""
    title, intro, fields = render(edition)
    return {
        "title": title,
        "intro": intro,
        "fields": [{"name": n, "value": v} for n, v in fields],
    }


__all__ = ["SOURCES", "edition_json", "gather_edition", "gazette_embed", "post_edition"]
