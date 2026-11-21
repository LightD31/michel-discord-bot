"""Pydantic models for the suggestions feature."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SuggestionStatus = Literal["pending", "approved", "denied", "implemented"]
VoteDirection = Literal["up", "down"]


class Suggestion(BaseModel):
    """A single suggestion submitted via ``/suggerer`` or "💡 En faire une suggestion"."""

    object_id: str | None = Field(default=None, alias="_id")
    id: int  # human-readable, auto-incrementing per guild
    guild_id: str
    channel_id: str
    message_id: str | None = None
    author_id: str
    text: str
    # The form's short title (P7); older suggestions have none.
    title: str | None = None
    # Made from a message ("💡 En faire une suggestion"): a link to it.
    source_url: str | None = None
    # The "Discussion #12" thread opened on its post, when the server wants one.
    thread_id: str | None = None
    status: SuggestionStatus = "pending"
    reason: str | None = None
    decided_by: str | None = None
    votes: dict[str, VoteDirection] = Field(default_factory=dict)  # Discord user id -> vote
    created_at: datetime

    model_config = {"populate_by_name": True}

    def tally(self) -> tuple[int, int]:
        """Return (up_count, down_count)."""
        up = sum(1 for v in self.votes.values() if v == "up")
        down = sum(1 for v in self.votes.values() if v == "down")
        return up, down


# What a decision may follow: an approved idea can still be marked implemented.
DECISIONS_FROM: dict[SuggestionStatus, tuple[SuggestionStatus, ...]] = {
    "approved": ("pending",),
    "denied": ("pending",),
    "implemented": ("pending", "approved"),
}


def next_decisions(status: SuggestionStatus) -> list[SuggestionStatus]:
    """The decisions staff may still take on a suggestion in *status*."""
    return [to for to, before in DECISIONS_FROM.items() if status in before]
