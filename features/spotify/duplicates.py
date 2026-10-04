"""Collapse tracks that appear more than once in a Spotify playlist.

Spotify happily stores the same track twice, which happens when someone
adds a song both from the Spotify app and through ``/addsong``. The bot's
playlist mirror is keyed by track id, so it never sees the second copy.

The Web API removes a track by URI, i.e. every occurrence at once, so a
duplicate is fixed by removing the track and re-inserting it once where its
first copy was.
"""

from collections import Counter
from collections.abc import Sequence


def plan_duplicate_removal(track_ids: Sequence[str | None]) -> list[tuple[str, int]]:
    """Return the ``(track_id, position)`` re-insertions that dedupe *track_ids*.

    Apply them in order: remove every occurrence of ``track_id``, then insert
    it back at ``position``. Each position accounts for the operations before
    it, so the result keeps the first occurrence of every track in place.
    Entries without an id (local files) still count for positions but are
    never touched.
    """
    counts = Counter(track_ids)
    ids = list(track_ids)
    plan: list[tuple[str, int]] = []
    for track_id in dict.fromkeys(track_ids):
        if track_id is None or counts[track_id] < 2:
            continue
        first = ids.index(track_id)
        ids = [i for i in ids if i != track_id]
        ids.insert(first, track_id)
        plan.append((track_id, first))
    return plan
