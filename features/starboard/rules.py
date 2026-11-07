"""Starboard rules: which reaction counts, and what happens to the mirror.

Pure functions over plain values, used by ``extensions/starboard/listeners.py``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from enum import StrEnum
from typing import Any

# interactions.py renders a custom emoji as <:name:id> (<a:name:id> if animated).
_CUSTOM_EMOJI = re.compile(r"^<a?:(?P<name>\w+):(?P<id>\d+)>$")
# Emoji presentation selector: "⭐" and "⭐️" are the same reaction.
_VARIATION_SELECTOR = "️"


class MirrorAction(StrEnum):
    """What to do with a message's starboard mirror after a reaction changed."""

    POST = "post"
    REFRESH = "refresh"
    REMOVE = "remove"
    NOTHING = "nothing"


def emoji_matches(reaction: str, configured: str) -> bool:
    """Whether a reaction is the configured emoji.

    *configured* is a Unicode emoji, a custom emoji's name (``star2`` or
    ``:star2:``), its id, or its full ``<:star2:123>`` form.
    """
    configured = configured.strip()
    if reaction.replace(_VARIATION_SELECTOR, "") == configured.replace(_VARIATION_SELECTOR, ""):
        return True
    custom = _CUSTOM_EMOJI.match(reaction)
    if custom is None:
        return False
    full = _CUSTOM_EMOJI.match(configured)
    if full is not None:
        return full["id"] == custom["id"]
    return configured.strip(":") in (custom["name"], custom["id"])


def count_valid_reactors(
    users: Iterable[Any], *, author_id: Any, allow_self: bool, ignore_bots: bool
) -> int:
    """Reactors who count towards the threshold: bots and the author are left out unless allowed."""
    return sum(
        1
        for user in users
        if not (ignore_bots and getattr(user, "bot", False))
        and (allow_self or user.id != author_id)
    )


def plan_mirror(
    stored_count: int | None, count: int, threshold: int, *, remove_below: bool
) -> MirrorAction:
    """The mirror update for a message now at *count* valid reactions.

    *stored_count* is the count recorded with its mirror, or ``None`` when the
    message isn't on the starboard yet. A mirror that falls below the threshold
    is removed only if the guild asked for it; otherwise its count is kept up to date.
    """
    if stored_count is None:
        return MirrorAction.POST if count >= threshold else MirrorAction.NOTHING
    if count < threshold and remove_below:
        return MirrorAction.REMOVE
    return MirrorAction.NOTHING if count == stored_count else MirrorAction.REFRESH


def first_image_url(message: Any) -> str | None:
    """The first image attached to or embedded in a message, if any."""
    for attachment in getattr(message, "attachments", None) or []:
        content_type = (getattr(attachment, "content_type", None) or "").lower()
        url = getattr(attachment, "url", "") or ""
        if content_type.startswith("image/"):
            return url
        if url.lower().rsplit("?", 1)[0].endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
            return url
    for embed in getattr(message, "embeds", None) or []:
        for media in (getattr(embed, "image", None), getattr(embed, "thumbnail", None)):
            if media is not None and getattr(media, "url", None):
                return media.url
    return None


__all__ = [
    "MirrorAction",
    "count_valid_reactors",
    "emoji_matches",
    "first_image_url",
    "plan_mirror",
]
