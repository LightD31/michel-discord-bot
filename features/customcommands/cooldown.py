"""How long a member waits before running a custom command again.

Kept in memory: a restart lets everyone run every command once more, which is
harmless for a GIF. Keyed by guild, command and member.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable

# Forget the uses older than the longest cooldown when the table grows past this.
_PRUNE_AT = 5000


class Cooldowns:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._last: dict[tuple[str, str, str], float] = {}
        self._longest = 0

    def remaining(self, guild_id: str, command: str, user_id: str, seconds: int) -> int:
        """Seconds left before *user_id* may run *command* again (0: now)."""
        if seconds <= 0:
            return 0
        last = self._last.get((guild_id, command, user_id))
        if last is None:
            return 0
        return max(0, math.ceil(last + seconds - self._clock()))

    def use(self, guild_id: str, command: str, user_id: str, seconds: int) -> None:
        """Record a use of a command that has a cooldown."""
        if seconds <= 0:
            return
        now = self._clock()
        self._longest = max(self._longest, seconds)
        if len(self._last) >= _PRUNE_AT:
            self._last = {k: t for k, t in self._last.items() if t + self._longest > now}
        self._last[(guild_id, command, user_id)] = now


__all__ = ["Cooldowns"]
