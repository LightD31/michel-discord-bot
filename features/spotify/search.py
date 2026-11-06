"""Autocomplete choices for the Spotify song pickers (``/addsong``, ``/addwithvote``).

Pure formatting: the extension runs the search and hands the raw items here.
"""

from typing import Any

# Discord caps a choice's name at 100 characters.
MAX_CHOICE_NAME = 100
# Sentinel value the commands treat as "no real track picked".
NO_TRACK = "error"

PROMPT = "Veuillez entrer un nom de chanson"
NO_RESULT = "Aucun résultat"
UNAVAILABLE = "Spotify ne répond pas — réessayez dans un instant"


def placeholder(message: str) -> list[dict[str, str]]:
    """A single unpickable choice carrying *message*."""
    return [{"name": message, "value": NO_TRACK}]


def track_choices(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Turn Spotify search items into ``{"name", "value"}`` autocomplete choices."""
    if not items:
        return placeholder(NO_RESULT)
    return [
        {
            "name": (
                f"{item['artists'][0]['name']} - {item['name']} (Album: {item['album']['name']})"
            )[:MAX_CHOICE_NAME],
            "value": item["uri"],
        }
        for item in items
    ]


__all__ = ["NO_RESULT", "NO_TRACK", "PROMPT", "UNAVAILABLE", "placeholder", "track_choices"]
