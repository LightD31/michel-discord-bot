"""Pydantic model for the giveaway feature."""

from datetime import datetime

from pydantic import BaseModel, Field

# Discord caps reactions per message at 20 unique emojis; we only use one
# (configurable). Winner count is bounded so the draw embed stays readable.
MAX_WINNERS = 20


class Giveaway(BaseModel):
    """A scheduled giveaway persisted to MongoDB.

    Entry tracking is intentionally indirect: we don't mirror Discord's
    reaction list into Mongo. At draw time the scheduler refetches the message
    and reads ``message.get_reaction(emoji).users()`` so the source of truth
    stays Discord — votes added/removed while the bot is offline still count.
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
    winners: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}
