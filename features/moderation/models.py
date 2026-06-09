"""Pydantic models for the moderation feature."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

InfractionType = Literal["warn", "timeout", "untimeout", "kick", "ban", "unban", "note", "automod"]
InfractionSource = Literal["manual", "automod"]


class Infraction(BaseModel):
    """A single moderation action recorded against a member."""

    object_id: str | None = Field(default=None, alias="_id")
    id: int  # human-readable, auto-incrementing per guild (case number)
    guild_id: str
    user_id: str  # the offending member
    moderator_id: str  # "automod" sentinel for automated actions
    type: InfractionType
    reason: str | None = None
    duration_seconds: int | None = None  # for timeout (and any future tempban)
    active: bool = True  # set False when a case is revoked
    source: InfractionSource = "manual"
    created_at: datetime
    expires_at: datetime | None = None  # for timeouts

    model_config = {"populate_by_name": True}
