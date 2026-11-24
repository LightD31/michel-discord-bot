"""What the member space shows a member of one server, read for them alone.

Every function here takes the member's id from the session (the route passes
``session.user_id``) and reads only what is theirs: their XP line, their
reminders, their birthday, their Tricount balances, their Secret Santa wish
list. Nothing new is stored: each part is read from the collection its module
keeps, and only where that module is on for the server (:data:`SECTIONS`).

The names of other members a part needs (who the member owes money to) are
resolved on the bot loop by the route; the functions here return their ids.
"""

from __future__ import annotations

from typing import Any

from features.achievements import AchievementsRepository, active_badges
from features.agenda import bounded_days
from features.birthday import BirthdayRepository, has_year
from features.reminders import MAX_REMINDERS_PER_MEMBER, ReminderRepository, schedule_label
from features.secretsanta import SecretSantaRepository
from features.secretsanta.dates import MONTH_NAMES, format_exchange_date
from features.tricount.balances import compute_balances, settlement_plan
from features.tricount.repository import TricountRepository
from features.xp import XpRepository, calculate_level
from features.xp.seasons import season_of
from src.core import clock
from src.core.config import ModuleConfig

# Section → the module that must be on for the server to show it.
SECTIONS: dict[str, str] = {
    "xp": "moduleXp",
    "reminders": "moduleUtils",
    "birthday": "moduleBirthday",
    "tricount": "moduleTricount",
    "santa": "moduleSecretSanta",
    "badges": "moduleAchievements",
    "agenda": "moduleAgenda",
}
DEFAULT_CURRENCY = "€"


def sections_on(guild_id: str) -> list[str]:
    """The member space's sections the server has, in display order."""
    return [name for name, module in SECTIONS.items() if ModuleConfig(module).enabled(guild_id)]


async def xp_card(guild_id: str, user_id: str) -> dict[str, Any]:
    """Level, rank, progress and this season's XP, as ``/profil`` shows them."""
    repo = XpRepository(guild_id)
    stats = await repo.get_user(user_id)
    xp = int(stats.get("xp", 0)) if stats else 0
    level, in_level, to_next = calculate_level(xp)
    card: dict[str, Any] = {
        "xp": xp,
        "level": level,
        "xpInLevel": in_level,
        "xpToNext": to_next,
        "rank": await repo.get_user_rank(user_id) if stats else None,
        "ranked": await repo.count_ranked(),
        "season": None,
    }
    settings = ModuleConfig("moduleXp").get(guild_id)
    if settings.get("seasonsEnabled"):
        season = season_of(clock.now(), settings.get("seasonMonths", 1))
        card["season"] = {
            "label": season.label,
            "xp": await repo.season_xp(user_id, season.start, season.end),
        }
    return card


async def reminders(guild_id: str, user_id: str) -> dict[str, Any]:
    """The member's reminders on the server, soonest first."""
    found = await ReminderRepository(guild_id).list_for_user(user_id)
    return {
        "limit": MAX_REMINDERS_PER_MEMBER,
        "items": [
            {
                "id": r.id,
                "message": r.message,
                "at": r.remind_time.isoformat(),
                "frequency": r.frequency,
                "label": schedule_label(r),
                "jumpUrl": r.jump_url,
            }
            for r in found
        ],
    }


async def birthday(guild_id: str, user_id: str) -> dict[str, Any] | None:
    """The member's birthday on the server, ``None`` when they haven't given it."""
    entry = await BirthdayRepository(guild_id).find_one(int(user_id))
    if entry is None:
        return None
    day = entry.date
    with_year = has_year(day)
    label = f"{day.day} {MONTH_NAMES[day.month - 1]}" + (f" {day.year}" if with_year else "")
    typed = f"{day.day:02d}/{day.month:02d}" + (f"/{day.year}" if with_year else "")
    return {
        "date": typed,
        "label": label,
        "hasYear": with_year,
        "hideYear": entry.hideyear,
        "timezone": entry.timezone,
    }


def currency(guild_id: str) -> str:
    return str(ModuleConfig("moduleTricount").get(guild_id).get("tricountCurrency") or "") or (
        DEFAULT_CURRENCY
    )


async def tricount(guild_id: str, user_id: str) -> list[dict[str, Any]]:
    """Each group the member is on the books of: their balance, and who pays whom for them.

    ``owes`` and ``owed`` are the lines of the group's settlement plan (the one
    ``/tricount bilan`` shows) where the member pays or is paid; the other
    members' balances are not returned.
    """
    uid = int(user_id)
    repo = TricountRepository(guild_id)
    out = []
    for group in await repo.groups_on_the_books(uid):
        balances = compute_balances(await repo.list_group_expenses(group["_id"]), group["members"])
        plan = settlement_plan(balances)
        mine = balances.get(uid, 0)
        if uid not in group["members"] and not mine:
            continue  # a former member, settled: no longer on the books
        out.append(
            {
                "id": str(group["_id"]),
                "name": group.get("name", ""),
                "balance": mine,
                "owes": [{"to": str(c), "cents": n} for d, c, n in plan if d == uid],
                "owed": [{"from": str(d), "cents": n} for d, c, n in plan if c == uid],
            }
        )
    return out


async def santa(guild_id: str, user_id: str) -> dict[str, Any] | None:
    """The server's Secret Santa as the member sees it: taking part, their list, the date."""
    repo = SecretSantaRepository()
    context_id = f"guild_{guild_id}"
    session = await repo.get_session(context_id)
    if session is None:
        return None
    uid = int(user_id)
    taking_part = uid in session.participants
    mine = await repo.get_participant(context_id, uid) if taking_part else None
    return {
        "drawn": session.is_drawn,
        "participant": taking_part,
        "participants": len(session.participants),
        "wishlist": (mine.wishlist if mine else None) or "",
        "exchangeLabel": format_exchange_date(session.exchange_day)
        if session.exchange_day
        else None,
        "budget": session.budget,
        "shipping": session.shipping,
    }


async def badges(guild_id: str, user_id: str) -> list[dict[str, Any]]:
    """The server's badges, each with when the member won it (``None``: still to win)."""
    won = await AchievementsRepository(guild_id).badges_of(user_id)
    return [
        {
            "key": badge.key,
            "emoji": badge.emoji,
            "name": badge.name,
            "goal": badge.goal,
            "earnedAt": clock.as_utc(won[badge.key]).isoformat() if badge.key in won else None,
        }
        for badge in active_badges(guild_id)
    ]


async def agenda(guild_id: str, user_id: str) -> dict[str, Any]:  # noqa: ARG001 — same shape
    """The agenda is gathered on the bot loop (``GET /api/me/servers/{id}/agenda``); the
    overview only says how many days it covers."""
    return {"days": bounded_days(ModuleConfig("moduleAgenda").get(guild_id).get("agendaDays"))}


__all__ = [
    "SECTIONS",
    "agenda",
    "badges",
    "birthday",
    "currency",
    "reminders",
    "santa",
    "sections_on",
    "tricount",
    "xp_card",
]
