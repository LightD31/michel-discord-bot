"""Who added a playlist track: Discord ids, and Spotify ids that slipped through.

``added_by`` is meant to hold a Discord user id. ``/addsong`` stores the
author's Discord id, but a track added from the Spotify app is attributed
through the guild's Spotify → Discord mapping, and an account missing from
the mapping at the time stores the raw Spotify user id instead. Such a track
never gets re-attributed on its own, so one person ends up split between
their Discord name and an unnamed contributor on the dashboard.
"""

import re
from collections.abc import Mapping

# Discord snowflakes are 17-20 digits; Spotify user ids are usernames, 25+
# character base62 strings, or (old Facebook-era accounts) ~10-digit numbers.
_DISCORD_ID = re.compile(r"\d{17,20}")


def is_discord_id(value: str) -> bool:
    return bool(_DISCORD_ID.fullmatch(value))


def reattribution_map(spotify2discord: Mapping[str, str]) -> dict[str, str]:
    """The ``spotify_id -> discord_id`` rewrites worth applying to stored tracks."""
    return {
        str(spotify_id): str(discord_id)
        for spotify_id, discord_id in spotify2discord.items()
        if spotify_id and discord_id and str(spotify_id) != str(discord_id)
    }


def unmapped_contributors(counts: Mapping[str, int]) -> dict[str, int]:
    """The ``added_by`` values (with track counts) that are not Discord ids."""
    return {added_by: n for added_by, n in counts.items() if not is_discord_id(added_by)}
