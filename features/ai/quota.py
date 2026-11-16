"""Cost guards for the AI features: who may ask, and how much per day.

``/ask`` exists only on the servers where the AI module is enabled, and every
answer is paid with the bot owner's API key. These limits, set in the global
``AI`` config section, bound what that can cost:

- ``paused`` — a kill switch for every AI answer (``/ask`` and mentions);
- ``dailyCallLimit`` / ``userDailyCallLimit`` — model calls per UTC day, for
  everyone together and per user. A comparison of three models counts three,
  a retry on another model counts one more. ``0`` means unlimited.

The app used to be user-installable, with ``externalUse`` rules for DMs and
other servers: anyone could install it and spend the shared daily budget, and
``/ask`` read channel history where no admin had turned the module on. It is
now a guild command, refused (``MODULE_DISABLED``) wherever the module is off.

Pure policy; the counters live in :mod:`features.ai.usage`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

DEFAULT_DAILY_CALLS = 1000
DEFAULT_USER_DAILY_CALLS = 60
MAX_LIMIT = 1_000_000

PAUSED = "L'IA de Michel est temporairement désactivée."
MODULE_DISABLED = "/ask n'est disponible que sur les serveurs où le module IA est activé."
USER_LIMIT_REACHED = "Tu as atteint ta limite quotidienne de questions à Michel — réessaie demain."
GLOBAL_LIMIT_REACHED = "Michel a atteint sa limite quotidienne de questions — réessaie demain."
QUOTA_UNAVAILABLE = "Impossible de vérifier les quotas de l'IA pour le moment — réessaie plus tard."

# Which counter a reservation ran into.
USER = "user"
GLOBAL = "global"


def _limit(value: Any, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(number, 0), MAX_LIMIT)


@dataclass(frozen=True)
class AiLimits:
    paused: bool = False
    daily_calls: int = DEFAULT_DAILY_CALLS
    user_daily_calls: int = DEFAULT_USER_DAILY_CALLS

    @classmethod
    def from_section(cls, section: Mapping[str, Any]) -> AiLimits:
        """Parse the limits out of the global ``AI`` section; bad values use the defaults."""
        return cls(
            paused=bool(section.get("paused", False)),
            daily_calls=_limit(section.get("dailyCallLimit"), DEFAULT_DAILY_CALLS),
            user_daily_calls=_limit(section.get("userDailyCallLimit"), DEFAULT_USER_DAILY_CALLS),
        )


def access_refusal(limits: AiLimits, *, in_enabled_guild: bool) -> str | None:
    """Why no AI answer may be given here at all, or ``None``.

    *in_enabled_guild* is false between a module switched off and the reload
    that removes ``/ask`` from that server.
    """
    if limits.paused:
        return PAUSED
    if not in_enabled_guild:
        return MODULE_DISABLED
    return None


def over_limit(count: int, limit: int) -> bool:
    """True if *count* calls exceed *limit* (``0`` = unlimited)."""
    return limit > 0 and count > limit


def limit_message(which: str) -> str:
    return USER_LIMIT_REACHED if which == USER else GLOBAL_LIMIT_REACHED


def day_key(now: datetime) -> str:
    """The UTC day a call counts toward."""
    return now.astimezone(UTC).strftime("%Y-%m-%d")


def day_expiry(now: datetime) -> datetime:
    """When a day's counters can be dropped: a full day after that day ends."""
    start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return start + timedelta(days=2)


__all__ = [
    "DEFAULT_DAILY_CALLS",
    "DEFAULT_USER_DAILY_CALLS",
    "GLOBAL",
    "MODULE_DISABLED",
    "QUOTA_UNAVAILABLE",
    "USER",
    "AiLimits",
    "access_refusal",
    "day_expiry",
    "day_key",
    "limit_message",
    "over_limit",
]
