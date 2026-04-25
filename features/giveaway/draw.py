"""Pure helpers for picking giveaway winners.

Kept separate from the Discord-facing extension so the selection logic is
trivially unit-testable without an event loop or mock client.
"""

from __future__ import annotations

import secrets


def pick_winners(
    entrants: list[str],
    count: int,
    *,
    exclude: list[str] | None = None,
) -> list[str]:
    """Return up to ``count`` distinct winners drawn uniformly from *entrants*.

    Uses :mod:`secrets` rather than :mod:`random` so the draw can't be
    predicted from a leaked PRNG seed — overkill for hobby giveaways but free.

    Parameters
    ----------
    entrants:
        Discord user IDs that reacted. Duplicates are dropped.
    count:
        Number of winners to pick. If fewer than ``count`` valid entrants
        remain after excluding ``exclude``, returns whatever is available.
    exclude:
        IDs that must not be picked (e.g. previous winners during a reroll, or
        the host if they accidentally reacted to their own giveaway).
    """
    if count <= 0 or not entrants:
        return []
    excluded = set(exclude or [])
    pool: list[str] = []
    seen: set[str] = set()
    for uid in entrants:
        if uid in seen or uid in excluded:
            continue
        seen.add(uid)
        pool.append(uid)
    if not pool:
        return []
    if count >= len(pool):
        # Use a Fisher-Yates shuffle from the cryptographic RNG so the result
        # order is also unpredictable.
        result = pool.copy()
        for i in range(len(result) - 1, 0, -1):
            j = secrets.randbelow(i + 1)
            result[i], result[j] = result[j], result[i]
        return result
    winners: list[str] = []
    remaining = pool.copy()
    for _ in range(count):
        idx = secrets.randbelow(len(remaining))
        winners.append(remaining.pop(idx))
    return winners


__all__ = ["pick_winners"]
