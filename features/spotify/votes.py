"""The playlist's polls: the daily keep/remove vote and the add-with-vote polls.

Pure rules over the stored votes (``{user_id: choice}``); the extension reads
and writes them (:class:`features.spotify.SpotifyRepository`) and renders the
messages.
"""

from __future__ import annotations

import random
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from features.spotify.vote_reminder import MIN_VOTES

DailyOutcome = Literal["extend", "remove", "keep"]

#: Without a single keep or remove vote, this many "menfou" votes remove the song.
MENFOU_REMOVES_AT = 3


@dataclass(frozen=True)
class DailyTally:
    """The daily poll's votes, and who cast them (first names where known)."""

    conserver: int
    supprimer: int
    menfou: int
    voters: list[str]

    @property
    def total(self) -> int:
        return self.conserver + self.supprimer + self.menfou


def voter_names(votes: Mapping[str, Any], names: Mapping[str, Any], *, unknown: str) -> list[str]:
    """Each voter's first name from *names*, else *unknown* formatted with their id."""
    return [str(names.get(user) or unknown.format(id=user)) for user in votes]


def tally_daily_votes(votes: Mapping[str, str], names: Mapping[str, Any]) -> DailyTally:
    """Count the ``conserver`` / ``supprimer`` / ``menfou`` votes of the daily poll."""
    choices = list(votes.values())
    return DailyTally(
        conserver=choices.count("conserver"),
        supprimer=choices.count("supprimer"),
        menfou=choices.count("menfou"),
        voters=voter_names(votes, names, unknown="{id}"),
    )


def daily_outcome(tally: DailyTally, *, min_votes: int = MIN_VOTES) -> DailyOutcome:
    """What closing the daily poll does with its song.

    Below *min_votes* votes the poll is extended by a day. Otherwise the song
    is removed when more people want it gone than kept, or when nobody took a
    side and enough people don't care; it is kept in every other case (a tie
    included).
    """
    if tally.total < min_votes:
        return "extend"
    if tally.supprimer > tally.conserver:
        return "remove"
    if tally.conserver == 0 and tally.supprimer == 0 and tally.menfou >= MENFOU_REMOVES_AT:
        return "remove"
    return "keep"


def pick_vote_track(
    track_ids: Collection[str], voted_ids: Collection[str], rng: random.Random | None = None
) -> str | None:
    """A random playlist track never put to the vote, ``None`` once all have been."""
    candidates = sorted(set(track_ids) - set(voted_ids))
    if not candidates:
        return None
    return (rng or random).choice(candidates)


def tally_add_votes(
    votes: Mapping[str, str], names: Mapping[str, Any]
) -> tuple[int, int, list[str]]:
    """``(yes, no, voters)`` of an add-with-vote poll; unnamed voters are mentioned."""
    choices = list(votes.values())
    return choices.count("yes"), choices.count("no"), voter_names(votes, names, unknown="<@{id}>")


def add_vote_passes(yes: int, no: int) -> bool:
    """An add-with-vote poll adds its song on a strict majority of yes."""
    return yes > no


def add_vote_deadline(now: datetime) -> datetime:
    """When an add-with-vote poll opened at *now* closes: the first full hour 24 h later."""
    deadline = (now + timedelta(days=1)).replace(minute=0, second=0, microsecond=0)
    if deadline < now + timedelta(days=1):
        deadline += timedelta(hours=1)
    return deadline


__all__ = [
    "MENFOU_REMOVES_AT",
    "DailyOutcome",
    "DailyTally",
    "add_vote_deadline",
    "add_vote_passes",
    "daily_outcome",
    "pick_vote_track",
    "tally_add_votes",
    "tally_daily_votes",
    "voter_names",
]
