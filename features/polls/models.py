"""Pydantic models for the polls feature (button-based and ranked polls)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

PollMode = Literal["public", "anonymous", "ranked"]


class Poll(BaseModel):
    """A button-based poll persisted to MongoDB.

    ``votes`` maps Discord user IDs to a list of option indices:
    - public/anonymous: a one-element list (the chosen option)
    - ranked: ordered preference list (first item = top choice)
    """

    id: str | None = Field(default=None, alias="_id")
    channel_id: str
    message_id: str
    author_id: str
    question: str
    options: list[str]
    mode: PollMode = "public"
    closes_at: datetime | None = None
    closed: bool = False
    votes: dict[str, list[int]] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}
