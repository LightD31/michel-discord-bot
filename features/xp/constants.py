"""Constants for the XP feature — award windows, cooldowns, display."""

XP_COOLDOWN_SECONDS = 60
XP_MIN = 15
XP_MAX = 25

LEADERBOARD_PAGE_SIZE = 10
RANK_MEDALS: list[str] = ["🥇", "🥈", "🥉"]

DEFAULT_LEVEL_UP_MESSAGE = "Bravo {mention}, tu as atteint le niveau {lvl} !"

USER_CACHE_TTL = 300  # 5 minutes
RANK_CACHE_TTL = 60  # 1 minute

__all__ = [
    "DEFAULT_LEVEL_UP_MESSAGE",
    "LEADERBOARD_PAGE_SIZE",
    "RANK_CACHE_TTL",
    "RANK_MEDALS",
    "USER_CACHE_TTL",
    "XP_COOLDOWN_SECONDS",
    "XP_MAX",
    "XP_MIN",
]
