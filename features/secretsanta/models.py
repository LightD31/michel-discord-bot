"""Domain model for a Secret Santa session and its participants."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from src.core import clock


@dataclass
class SecretSantaSession:
    """Represents an active Secret Santa session.

    ``exchange_date`` is the day gifts are given (``YYYY-MM-DD``). ``shipping``
    is for a session whose gifts travel by post: it adds the "sent" /
    "received" marks and the reminder before the date, which an in-person
    exchange has no use for. ``reminded`` records that reminder went out.
    """

    context_id: str
    channel_id: int
    message_id: int | None = None
    created_at: str = ""
    created_by: int = 0
    participants: list[int] = field(default_factory=list)
    is_drawn: bool = False
    budget: str | None = None
    exchange_date: str | None = None
    shipping: bool = False
    reminded: bool = False

    def __post_init__(self):
        if not self.created_at:
            self.created_at = clock.now().isoformat()

    @property
    def exchange_day(self) -> date | None:
        return date.fromisoformat(self.exchange_date) if self.exchange_date else None

    @property
    def created(self) -> datetime:
        return datetime.fromisoformat(self.created_at)


@dataclass
class SantaParticipant:
    """What one participant of a session left: their wish list and gift marks.

    ``sent_at``: when they, as a Santa, marked their gift sent; ``received_at``:
    when they marked the gift they were given as received.
    """

    context_id: str
    user_id: int
    wishlist: str | None = None
    sent_at: datetime | None = None
    received_at: datetime | None = None
