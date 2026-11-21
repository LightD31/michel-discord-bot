"""Models for the per-guild custom slash commands feature."""

from typing import Literal

from pydantic import BaseModel

# Whether the command takes a member to aim at (``/calin cible:@Léa``).
TargetMode = Literal["none", "optional", "required"]


class CustomCommand(BaseModel):
    """One guild-defined slash command and the pool it answers from."""

    name: str
    description: str
    responses: list[str]
    target: TargetMode = "none"
    # Seconds a member waits between two uses (0: none).
    cooldown: int = 0
    # Role ids allowed to use it (empty: everyone).
    roles: list[str] = []


class ParsedCommands(BaseModel):
    """Result of reading a guild's ``commands`` config list.

    ``errors`` collects human-readable reasons for every entry that was
    dropped, so the extension can log them without failing the whole guild.
    """

    commands: list[CustomCommand] = []
    errors: list[str] = []
