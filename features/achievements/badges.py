"""The badges (succès) a member can unlock, and what each one asks for (P18).

Each badge reads what one module already keeps (the starboard's mirrors, the
voice XP ticks, the playlist, suggestions, the feur counter) or the date the
member joined, so nothing new is counted. A badge is checked only where its
module is on and the admins left it on (:attr:`Badge.setting`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from features.xp.constants import VOICE_TICK_SECONDS
from src.core.config import ModuleConfig

VOICE_HOURS = 10
VOICE_TICKS = VOICE_HOURS * 3600 // VOICE_TICK_SECONDS  # one xp_events row per voice tick
DJ_TRACKS = 10
FEUR_COUNT = 100
SENIORITY = timedelta(days=365)


@dataclass(frozen=True)
class Badge:
    key: str
    emoji: str
    name: str
    goal: str  # what it asks for, shown with the badges still to win
    module: str  # the module whose data it reads
    setting: str  # the moduleAchievements switch that turns it off

    @property
    def label(self) -> str:
        return f"{self.emoji} {self.name}"


BADGES: tuple[Badge, ...] = (
    Badge("star", "⭐", "Étoilé·e", "un message au starboard", "moduleStarboard", "badgeStar"),
    Badge(
        "voice",
        "🎙️",
        "Pilier du vocal",
        f"{VOICE_HOURS} h de vocal sur l'année",
        "moduleXp",
        "badgeVoice",
    ),
    Badge(
        "dj", "🎵", "DJ", f"{DJ_TRACKS} titres ajoutés à la playlist", "moduleSpotify", "badgeDj"
    ),
    Badge(
        "ideas",
        "💡",
        "Boîte à idées",
        "une suggestion mise en place",
        "moduleSuggestions",
        "badgeIdeas",
    ),
    Badge(
        "seniority",
        "🎂",
        "Vieille branche",
        "un an sur le serveur",
        "moduleAchievements",
        "badgeSeniority",
    ),
    Badge(
        "feur",
        "🇫",
        f"Feur ×{FEUR_COUNT}",
        f"{FEUR_COUNT} « feur » encaissés",
        "moduleFeur",
        "badgeFeur",
    ),
)
BY_KEY = {badge.key: badge for badge in BADGES}


def badge_of(key: str) -> Badge | None:
    return BY_KEY.get(key)


def active_badges(guild_id: object) -> list[Badge]:
    """The badges *guild_id* plays with: succès on, the badge left on, its module on."""
    achievements = ModuleConfig("moduleAchievements")
    if not achievements.enabled(guild_id):
        return []
    settings = achievements.get(guild_id)
    return [
        badge
        for badge in BADGES
        if settings.get(badge.setting, True) is not False
        and ModuleConfig(badge.module).enabled(guild_id)
    ]


async def badge_labels(
    guild_id: object, user_ids: list[str]
) -> dict[str, list[dict[str, str]]] | None:
    """Each member's active badges as ``{emoji, name}``, for a leaderboard; ``None`` when
    the server plays with none (the rows then carry no ``badges`` key)."""
    from features.achievements.repository import AchievementsRepository

    playing = active_badges(guild_id)
    if not playing:
        return None
    won = await AchievementsRepository(str(guild_id)).badges_of_many(user_ids)
    return {
        uid: [{"emoji": b.emoji, "name": b.name} for b in playing if b.key in set(keys)]
        for uid, keys in won.items()
    }


__all__ = [
    "BADGES",
    "BY_KEY",
    "DJ_TRACKS",
    "FEUR_COUNT",
    "SENIORITY",
    "VOICE_HOURS",
    "VOICE_TICKS",
    "Badge",
    "active_badges",
    "badge_labels",
    "badge_of",
]
