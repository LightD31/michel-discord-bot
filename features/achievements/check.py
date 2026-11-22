"""One pass over a server's badges: who qualifies, who is new (P18, A3).

Pure orchestration over :class:`AchievementsRepository`, so the Discord layer
only brings what the database can't tell (who has been on the server a year)
and posts the result.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from features.achievements.badges import Badge
from features.achievements.repository import AchievementsRepository

# Badge -> the repository method naming who qualifies ("seniority" comes from Discord).
QUALIFYING: dict[str, str] = {
    "star": "qualifying_star",
    "voice": "qualifying_voice",
    "dj": "qualifying_dj",
    "ideas": "qualifying_ideas",
    "feur": "qualifying_feur",
}


async def check_badges(
    repo: AchievementsRepository,
    badges: Iterable[Badge],
    seniors: set[str],
    now: datetime,
) -> list[tuple[str, Badge]]:
    """Award *badges* to whoever qualifies; the ``(member, badge)`` pairs to announce.

    *seniors* are the members on the server for a year (the Discord layer
    knows when they joined). The first check of a badge on the server awards
    in silence: those members earned it before the badge existed here.
    """
    seeded = await repo.seeded()
    news: list[tuple[str, Badge]] = []
    for badge in badges:
        if badge.key == "seniority":
            qualifying = seniors
        else:
            qualifying = await getattr(repo, QUALIFYING[badge.key])()
        new = await repo.award(badge.key, qualifying, now)
        if badge.key in seeded:
            news.extend((uid, badge) for uid in new)
        else:
            await repo.mark_seeded(badge.key, now)
    return news


__all__ = ["QUALIFYING", "check_badges"]
