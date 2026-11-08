"""What changed in a streamer's emotes since the last check.

Pure functions over the emotes Twitch returns now (anything with ``id`` and
``name``) and the ones stored at the previous check (``{emote_id: {"name": …}}``);
``extensions/twitch/emotes.py`` downloads the images and posts the notifications.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol


class EmoteLike(Protocol):
    id: str
    name: str


@dataclass
class EmoteDiff[E: EmoteLike]:
    """New, replaced (same name, new id) and removed emotes."""

    added: list[E] = field(default_factory=list)
    replaced: list[tuple[str, E]] = field(default_factory=list)  # (old id, new emote)
    removed: list[str] = field(default_factory=list)  # stored ids

    def __bool__(self) -> bool:
        return bool(self.added or self.replaced or self.removed)


def diff_emotes[E: EmoteLike](
    current: Sequence[E], stored: Mapping[str, Mapping[str, Any]]
) -> EmoteDiff[E]:
    """Compare Twitch's emotes with the stored ones.

    An emote whose id is new but whose name belonged to a vanished emote was
    *replaced* (the streamer re-uploaded it); one with a new name was *added*;
    a stored emote whose name no longer exists was *removed*.
    """
    current_ids = {emote.id for emote in current}
    current_names = {emote.name for emote in current}
    gone = [emote_id for emote_id in stored if emote_id not in current_ids]
    gone_by_name = {stored[emote_id].get("name"): emote_id for emote_id in gone}

    diff: EmoteDiff[E] = EmoteDiff()
    for emote in current:
        if emote.id in stored:
            continue
        if emote.name in gone_by_name:
            diff.replaced.append((gone_by_name[emote.name], emote))
        else:
            diff.added.append(emote)
    diff.removed = [
        emote_id for emote_id in gone if stored[emote_id].get("name") not in current_names
    ]
    return diff


def emote_image_url(images: Mapping[str, str] | None) -> str | None:
    """The largest image Twitch offers for an emote."""
    images = images or {}
    return images.get("url_4x") or images.get("url_2x") or images.get("url_1x")


def emote_kind(emote_type: str | None, tier: str | None) -> str:
    """How the emote is unlocked, as shown in the notification."""
    if emote_type == "subscriptions":
        return {"1000": "Sub tier 1", "2000": "Sub tier 2"}.get(tier or "", "Sub tier 3")
    if emote_type == "bitstier":
        return "Bits"
    if emote_type == "follower":
        return "Follower"
    return "Autre"


__all__ = ["EmoteDiff", "EmoteLike", "diff_emotes", "emote_image_url", "emote_kind"]
