"""La Gazette du dimanche (P5, A1): what happened on the server this week.

Pure: :class:`Edition` holds the week's sections, already gathered and named
(``src/discord_ext/gazette.py`` does that on the bot loop), and
:func:`render` turns them into a title, an intro and ``(heading, text)``
fields. A section with nothing in it is left out, so a quiet week makes a
short paper, and an empty one says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from features.secretsanta.dates import MONTH_NAMES, WEEKDAYS
from features.xp.seasons import thousands
from src.core import clock
from src.core.text import EMBED_FIELD_VALUE_LIMIT, clip

TOP_MEMBERS = 5
SHOWN_NEWCOMERS = 10
SHOWN_ITEMS = 5
WEEK = timedelta(days=7)
MIN_WEEKDAY, MAX_WEEKDAY = 1, 7  # lundi … dimanche
MIN_HOUR, MAX_HOUR = 0, 23
AI_INTRO_LIMIT = 600


@dataclass
class Edition:
    """One week of the server, every member already turned into a name."""

    guild_name: str
    start: datetime  # UTC, included
    end: datetime  # UTC, excluded
    top_members: list[tuple[str, int]] = field(default_factory=list)  # (name, XP)
    star: tuple[str, int, str] | None = None  # (author, stars, link)
    birthdays: list[tuple[str, date]] = field(default_factory=list)  # (name, next day)
    newcomers: list[str] = field(default_factory=list)
    tracks_added: int = 0
    top_dj: tuple[str, int] | None = None
    suggestions: list[tuple[int, str, str]] = field(default_factory=list)  # (#, status, text)
    giveaways: list[tuple[str, list[str]]] = field(default_factory=list)  # (prize, winners)
    feur: tuple[str, int] | None = None  # (name, times answered « feur »)
    ai_intro: str = ""  # a few lines the AI wrote, where the admins asked for them


def week_key(moment: datetime) -> str:
    """The ISO week an edition belongs to (``2026-W47``), on the Paris clock."""
    year, week, _ = clock.to_local(moment).isocalendar()
    return f"{year}-W{week:02d}"


def is_due(now: datetime, weekday: int, hour: int) -> bool:
    """Whether the week's edition is due at *now*: its day (1 = lundi), from its hour."""
    local = clock.to_local(now)
    return local.isoweekday() == weekday and local.hour >= hour


def _day(day: date) -> str:
    first = "1er" if day.day == 1 else str(day.day)
    return f"{first} {MONTH_NAMES[day.month - 1]}"


def week_label(start: datetime, end: datetime) -> str:
    """``du 16 au 22 novembre`` (the last day shown is the one before *end*)."""
    first = clock.to_local(start).date()
    last = clock.to_local(end - timedelta(seconds=1)).date()
    if first.month == last.month:
        return f"du {first.day if first.day > 1 else '1er'} au {_day(last)}"
    return f"du {_day(first)} au {_day(last)}"


def upcoming_birthdays(
    days: list[tuple[str, date]], today: date, horizon: int = 7
) -> list[tuple[str, date]]:
    """``(who, birth date)`` → ``(who, next birthday)`` within *horizon* days, soonest first.

    The year of birth is dropped: only the next day it falls on is kept (29
    February is the 28th in other years).
    """
    found = []
    for who, born in days:
        for year in (today.year, today.year + 1):
            try:
                next_day = born.replace(year=year)
            except ValueError:  # 29 February
                next_day = date(year, 2, 28)
            if next_day >= today:
                break
        if (next_day - today).days < horizon:
            found.append((who, next_day))
    return sorted(found, key=lambda pair: (pair[1], pair[0]))


STATUS_WORDS = {
    "approved": "✅ approuvée",
    "denied": "❌ refusée",
    "implemented": "🚀 mise en place",
}


def render(edition: Edition) -> tuple[str, str, list[tuple[str, str]]]:
    """``(title, intro, fields)`` of the edition, in French."""
    title = f"📰 La Gazette de {edition.guild_name}"
    intro = f"Ce qui s'est passé sur le serveur {week_label(edition.start, edition.end)}."
    fields: list[tuple[str, str]] = []
    if edition.top_members:
        medals = ("🥇", "🥈", "🥉")
        lines = [
            f"{medals[i] if i < 3 else f'{i + 1}.'} {name} — {thousands(xp)} XP"
            for i, (name, xp) in enumerate(edition.top_members[:TOP_MEMBERS])
        ]
        fields.append(("🔥 Les plus actifs", "\n".join(lines)))
    if edition.star:
        author, stars, link = edition.star
        fields.append(("⭐ Le message de la semaine", f"[Celui de {author}]({link}), {stars} ⭐"))
    if edition.newcomers:
        shown = edition.newcomers[:SHOWN_NEWCOMERS]
        more = len(edition.newcomers) - len(shown)
        text = ", ".join(shown) + (f" et {more} autre{'s' if more > 1 else ''}" if more > 0 else "")
        fields.append(("👋 Bienvenue", text))
    if edition.birthdays:
        lines = [
            f"{name} — {WEEKDAYS[day.weekday()]} {_day(day)}"
            for name, day in edition.birthdays[: SHOWN_ITEMS * 2]
        ]
        fields.append(("🎂 Anniversaires à venir", "\n".join(lines)))
    if edition.tracks_added:
        text = f"{edition.tracks_added} titre{'s' if edition.tracks_added > 1 else ''} ajouté"
        text += "s" if edition.tracks_added > 1 else ""
        if edition.top_dj:
            text += f", dont {edition.top_dj[1]} par {edition.top_dj[0]}"
        fields.append(("🎵 La playlist", text + "."))
    if edition.suggestions:
        lines = [
            f"#{number} {STATUS_WORDS.get(status, status)} : {clip(text, 80)}"
            for number, status, text in edition.suggestions[:SHOWN_ITEMS]
        ]
        fields.append(("💡 Suggestions tranchées", "\n".join(lines)))
    if edition.giveaways:
        lines = [
            f"{clip(prize, 60)} → {', '.join(winners) or 'personne'}"
            for prize, winners in edition.giveaways[:SHOWN_ITEMS]
        ]
        fields.append(("🎁 Giveaways", "\n".join(lines)))
    if edition.feur:
        name, times = edition.feur
        fields.append(("🇫 Le feur de la semaine", f"{name}, tombé·e {times} fois dans le panneau."))
    if not fields:
        intro += "\nUne semaine calme : rien à raconter cette fois-ci."
    elif edition.ai_intro:
        intro += "\n\n" + clip(edition.ai_intro, AI_INTRO_LIMIT)
    return title, intro, [(name, clip(value, EMBED_FIELD_VALUE_LIMIT)) for name, value in fields]


__all__ = [
    "AI_INTRO_LIMIT",
    "MAX_HOUR",
    "MAX_WEEKDAY",
    "MIN_HOUR",
    "MIN_WEEKDAY",
    "WEEK",
    "Edition",
    "is_due",
    "render",
    "upcoming_birthdays",
    "week_key",
    "week_label",
]
