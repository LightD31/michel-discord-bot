"""Pydantic models for the reminder feature."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Frequency = Literal["daily", "weekly", "monthly", "yearly"]

# Per member and server; /rappel supprimer's menu holds 25 options too.
MAX_REMINDERS_PER_MEMBER = 25
# A DM is capped at 2000 characters; snoozes prefix the text.
MAX_REMINDER_LENGTH = 1500


class Reminder(BaseModel):
    """A single scheduled reminder, sent by DM to the member who created it (``user_id``)."""

    id: str | None = Field(default=None, alias="_id")
    user_id: str
    message: str
    remind_time: datetime
    frequency: Frequency | None = None
    # Made from a message ("⏰ Me le rappeler"): the link the DM brings back to.
    jump_url: str | None = None

    model_config = {"populate_by_name": True}
