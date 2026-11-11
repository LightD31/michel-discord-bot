"""Pydantic models for the reminder feature."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Frequency = Literal["daily", "weekly", "monthly", "yearly"]


class Reminder(BaseModel):
    """A single scheduled reminder, sent by DM to the member who created it (``user_id``)."""

    id: str | None = Field(default=None, alias="_id")
    user_id: str
    message: str
    remind_time: datetime
    frequency: Frequency | None = None

    model_config = {"populate_by_name": True}
