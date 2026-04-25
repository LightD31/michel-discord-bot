"""Pydantic models for the suggestions feature."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SuggestionStatus = Literal["pending", "approved", "denied", "implemented"]


class Suggestion(BaseModel):
    """A single suggestion submitted via ``/suggest``."""

    object_id: str | None = Field(default=None, alias="_id")
    id: int  # human-readable, auto-incrementing per guild
    guild_id: str
    channel_id: str
    message_id: str | None = None
    author_id: str
    text: str
    status: SuggestionStatus = "pending"
    reason: str | None = None
    decided_by: str | None = None
    votes: dict[str, str] = Field(default_factory=dict)  # user_id -> "up" | "down"
    created_at: datetime

    model_config = {"populate_by_name": True}

    def tally(self) -> tuple[int, int]:
        """Return (up_count, down_count)."""
        up = sum(1 for v in self.votes.values() if v == "up")
        down = sum(1 for v in self.votes.values() if v == "down")
        return up, down
