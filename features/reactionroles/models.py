"""Pydantic models for the reaction-roles feature."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# "buttons": one button per role, a click gives or takes it. "select": one
# drop-down where the member ticks the roles they want (B3).
MenuStyle = Literal["buttons", "select"]


class RoleMenuEntry(BaseModel):
    """A single (role, emoji, label) tuple inside a role menu.

    The emoji may be empty: a menu built by picking roles in Discord has none.
    """

    role_id: str
    emoji: str = ""
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
    style: MenuStyle = "buttons"
    # How many of the menu's roles a member may hold at once; 1 is "one of the
    # group" (colour roles): a new pick replaces the old one. None: no limit.
    max_roles: int | None = Field(default=None, ge=1, le=25)
    created_by: str
    created_at: datetime

    model_config = {"populate_by_name": True}
