"""Pydantic models for the starboard feature."""

from datetime import datetime

from pydantic import BaseModel, Field


class StarEntry(BaseModel):
    """Maps an original message to its starboard mirror message."""

    id: str | None = Field(default=None, alias="_id")  # = original_message_id
    guild_id: str
    channel_id: str
    original_message_id: str
    mirror_channel_id: str
    mirror_message_id: str
    author_id: str
    count: int
    created_at: datetime

    model_config = {"populate_by_name": True}
