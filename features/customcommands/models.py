"""Models for the per-guild custom slash commands feature."""

from pydantic import BaseModel


class CustomCommand(BaseModel):
    """One guild-defined slash command and the pool it answers from."""

    name: str
    description: str
    responses: list[str]


class ParsedCommands(BaseModel):
    """Result of reading a guild's ``commands`` config list.

    ``errors`` collects human-readable reasons for every entry that was
    dropped, so the extension can log them without failing the whole guild.
    """

    commands: list[CustomCommand] = []
    errors: list[str] = []
