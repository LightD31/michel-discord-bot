"""Pydantic model for the giveaway feature."""

from datetime import datetime

from pydantic import BaseModel, Field

# Winner count is bounded so the draw embed stays readable.
MAX_WINNERS = 20


class Giveaway(BaseModel):
    """A scheduled giveaway persisted to MongoDB.

    Members enter with the announcement's "Participer" button: ``entrants``
    holds their ids, added and removed with one atomic update per click, and
    the draw picks among them. ``emoji`` decorates that button.
    """

    id: str | None = Field(default=None, alias="_id")
    guild_id: str
    channel_id: str
    message_id: str
    host_id: str
    prize: str
    description: str | None = None
    emoji: str = "🎉"
    allow_host_win: bool | None = None
    winners_count: int = 1
    ends_at: datetime
    drawn: bool = False
    cancelled: bool = False
    entrants: list[str] = Field(default_factory=list)
    winners: list[str] = Field(default_factory=list)
    # Draws given back because Discord didn't answer (5xx, timeout).
    draw_failures: int = 0

    model_config = {"populate_by_name": True}
