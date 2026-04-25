"""Pydantic models for the reminder feature."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Frequency = Literal["daily", "weekly", "monthly", "yearly"]


class Reminder(BaseModel):
    """A single scheduled reminder.

    ``user_id`` is the author/creator. ``recipient_ids`` enables shared/group
    reminders: every listed Discord user is DM'd when the reminder fires.
    Empty/missing ``recipient_ids`` means "DM the author only" — the legacy
    behaviour, preserved for documents written before this field existed.
    """

    id: str | None = Field(default=None, alias="_id")
    user_id: str
    message: str
    remind_time: datetime
    frequency: Frequency | None = None
    recipient_ids: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}

    def all_recipients(self) -> list[str]:
        """Recipients for this reminder, always including the author."""
        if not self.recipient_ids:
            return [self.user_id]
        unique: list[str] = []
        seen: set[str] = set()
        for uid in (self.user_id, *self.recipient_ids):
            if uid and uid not in seen:
                seen.add(uid)
                unique.append(uid)
        return unique
