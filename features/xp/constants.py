"""Constants for the XP feature — award windows, cooldowns, display."""

XP_COOLDOWN_SECONDS = 60
XP_MIN = 15
XP_MAX = 25

# Voice XP: awarded on a per-minute tick while the user is in a non-AFK voice
# channel with at least one other non-bot member. Same magnitude as messages,
# scaled lower since voice presence is passive.
VOICE_XP_PER_TICK_MIN = 5
VOICE_XP_PER_TICK_MAX = 10
VOICE_TICK_SECONDS = 180

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
    "VOICE_TICK_SECONDS",
    "VOICE_XP_PER_TICK_MAX",
    "VOICE_XP_PER_TICK_MIN",
    "XP_COOLDOWN_SECONDS",
    "XP_MAX",
    "XP_MIN",
]
