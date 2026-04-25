"""Pydantic models for the reaction-roles feature."""

from datetime import datetime

from pydantic import BaseModel, Field


class RoleMenuEntry(BaseModel):
    """A single (role, emoji, label) tuple inside a role menu."""

    role_id: str
    emoji: str
    label: str


class RoleMenu(BaseModel):
    """A persistent role-menu message attached to a Discord channel."""

    id: str | None = Field(default=None, alias="_id")
    guild_id: str
    channel_id: str
    message_id: str | None = None
    title: str
    description: str | None = None
    entries: list[RoleMenuEntry]
    created_by: str
    created_at: datetime

    model_config = {"populate_by_name": True}
