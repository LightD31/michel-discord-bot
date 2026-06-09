"""Moderation feature — infraction persistence and duration helpers.

Pure domain layer (no ``interactions`` imports) so it can be unit-tested.
"""

from features.moderation.duration import (
    MAX_TIMEOUT_SECONDS,
    clamp_timeout,
    humanize_duration,
    parse_duration,
)
from features.moderation.filters import INVITE_RE, contains_invite, match_banned_word
from features.moderation.models import Infraction, InfractionSource, InfractionType
from features.moderation.repository import ModerationRepository

__all__ = [
    "INVITE_RE",
    "MAX_TIMEOUT_SECONDS",
    "Infraction",
    "InfractionSource",
    "InfractionType",
    "ModerationRepository",
    "clamp_timeout",
    "contains_invite",
    "humanize_duration",
    "match_banned_word",
    "parse_duration",
]
