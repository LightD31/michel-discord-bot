"""Plain data structures shared by the Zevent stats parsers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Participant:
    """One participating channel as reported by the stats API."""

    display_name: str
    twitch_login: str
    twitch_id: str
    location: str
    """Bucket used by the embeds: ``"LAN"`` or ``"Online"``."""
    raw_location: str
    live: bool
    amount_raised: float
    """Euros (the API reports centimes)."""


@dataclass(frozen=True)
class Show:
    """One planning entry ("show") of the event calendar."""

    name: str
    description: str
    start: datetime | None
    end: datetime | None
    all_day: bool
    hosts: list[str] = field(default_factory=list)
    guests: list[str] = field(default_factory=list)
