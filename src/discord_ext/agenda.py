"""The agenda gathered on the bot loop, and its card (P19, A5).

Shared by ``/agenda``, the message the agenda keeps up to date and the
dashboard's calendar (``routes/agenda.py``). Each source is read only where its
module is on; Discord's scheduled events (the ones Twitch and the Zevent
manage too) come from Discord. A source that can't be read is left out rather
than failing the whole agenda; the result says which.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from interactions import ContainerComponent, Embed

from features.agenda import AgendaRepository, Entry, by_day, timed, window
from features.gazette import upcoming_birthdays
from src.core import clock
from src.core import logging as logutil
from src.core.config import ModuleConfig
from src.core.errors import DatabaseError
from src.core.logging import log_failure
from src.discord_ext.look import card, card_v2
from src.discord_ext.messages import event_url, jump_url

logger = logutil.init_logger(__name__)

SOURCES = {
    "birthday": ModuleConfig("moduleBirthday"),
    "giveaway": ModuleConfig("moduleGiveaway"),
    "poll": ModuleConfig("moduleUtils"),
    "santa": ModuleConfig("moduleSecretSanta"),
}
# Discord's scheduled event statuses still to come or under way (1 scheduled, 2 active).
LIVE_STATUSES = {1, 2}


@dataclass
class Agenda:
    guild_name: str
    now: datetime
    days: int
    entries: list[Entry] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)


def _member_name(guild: Any, user_id: Any) -> str | None:
    try:
        member = guild.get_member(int(user_id)) if guild is not None else None
    except (TypeError, ValueError):
        return None
    if member is None or getattr(member, "bot", False):
        return None
    return str(getattr(member, "display_name", None) or getattr(member, "username", user_id))


async def _events(guild: Any, guild_id: str, start: datetime, end: datetime) -> list[Entry]:
    found = []
    for event in await guild.list_scheduled_events():
        status = int(getattr(event, "status", 0) or 0)
        begins = getattr(event, "start_time", None)
        if status not in LIVE_STATUSES or begins is None:
            continue
        begins = clock.as_utc(begins)
        if begins >= end:
            continue
        # One under way shows on today, from now.
        at = max(begins, start)
        found.append(timed("event", str(event.name), at, event_url(guild_id, event.id)))
    return found


async def gather_agenda(bot: Any, guild_id: str, now: datetime, days: int) -> Agenda:
    """What's coming up in *guild_id* over *days* days from *now*."""
    guild = bot.get_guild(int(guild_id)) if str(guild_id).isdigit() else None
    agenda = Agenda(str(getattr(guild, "name", guild_id)), now, days)
    start, end = window(now, days)
    repo = AgendaRepository(guild_id)
    on = {name for name, module in SOURCES.items() if module.enabled(guild_id)}

    async def read(name: str, work: Any) -> None:
        try:
            agenda.entries.extend(await work())
        except DatabaseError as e:
            logger.warning("Agenda of guild %s: %s unreadable: %s", guild_id, name, e)
            agenda.unavailable.append(name)
        except Exception as e:  # Discord's own errors (the events list)
            log_failure(logger, "Agenda of guild %s: %s unreadable: %s", guild_id, name, e)
            agenda.unavailable.append(name)

    if guild is not None:
        await read("event", lambda: _events(guild, guild_id, start, end))

    async def birthdays() -> list[Entry]:
        named = [
            (n, born) for uid, born in await repo.birth_dates() if (n := _member_name(guild, uid))
        ]
        today = clock.to_local(now).date()
        return [
            Entry(day, "birthday", f"Anniversaire de {who}")
            for who, day in upcoming_birthdays(named, today, horizon=days + 1)
        ]

    async def giveaways() -> list[Entry]:
        return [
            timed(
                "giveaway",
                f"Fin du giveaway « {doc.get('prize', '')} »",
                clock.as_utc(doc["ends_at"]),
                jump_url(guild_id, doc["channel_id"], doc["message_id"]),
            )
            for doc in await repo.giveaways_ending(start, end)
        ]

    async def polls() -> list[Entry]:
        return [
            timed(
                "poll",
                f"Fin du sondage « {doc.get('question', '')} »",
                clock.as_utc(doc["closes_at"]),
                jump_url(guild_id, doc["channel_id"], doc["message_id"]),
            )
            for doc in await repo.polls_closing(start, end)
        ]

    async def santa() -> list[Entry]:
        day = await repo.santa_exchange()
        if day is None or not (clock.to_local(start).date() <= day <= clock.to_local(end).date()):
            return []
        return [Entry(day, "santa", "Échange des cadeaux du Père Noël secret")]

    readers = {"birthday": birthdays, "giveaway": giveaways, "poll": polls, "santa": santa}
    for name, reader in readers.items():
        if name in on:
            await read(name, reader)
    return agenda


def agenda_embed(agenda: Agenda) -> Embed:
    today = clock.to_local(agenda.now).date()
    days = by_day(agenda.entries, today)
    embed = card(
        "moduleAgenda",
        title=f"🗓️ Les {agenda.days} prochains jours",
        description=None if days else "Rien de prévu pour l'instant.",
        footer="mis à jour toutes les 30 minutes",
    )
    for label, lines in days[:25]:  # Discord's limit of fields
        embed.add_field(name=label, value="\n".join(lines)[:1024], inline=False)
    return embed


def agenda_card(agenda: Agenda) -> ContainerComponent:
    """The same days as a Components V2 card, for the message kept up to date (P22)."""
    today = clock.to_local(agenda.now).date()
    days = by_day(agenda.entries, today)
    return card_v2(
        "moduleAgenda",
        title=f"🗓️ Les {agenda.days} prochains jours",
        blocks=[f"**{label}**\n" + "\n".join(lines) for label, lines in days]
        or ["Rien de prévu pour l'instant."],
        footer="mis à jour toutes les 30 minutes",
    )


__all__ = ["SOURCES", "Agenda", "agenda_card", "agenda_embed", "gather_agenda"]
