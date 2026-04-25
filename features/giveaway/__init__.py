"""Giveaway feature — models, persistence, and pure draw logic.

The Discord-facing extension lives in ``extensions/giveaway``. Keeping the
feature package free of ``interactions.py`` imports lets the WebUI and tests
reuse it without dragging the bot framework in.
"""

from features.giveaway.draw import pick_winners
from features.giveaway.models import MAX_WINNERS, Giveaway

__all__ = [
    "MAX_WINNERS",
    "Giveaway",
    "GiveawayRepository",
    "pick_winners",
]


# ``GiveawayRepository`` pulls in pymongo at import time. Expose it lazily so
# pure-logic tests of ``draw`` and ``models`` don't need MongoDB drivers.
def __getattr__(name: str):  # noqa: D401 — module-level dunder
    if name == "GiveawayRepository":
        from features.giveaway.repository import GiveawayRepository

        return GiveawayRepository
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
