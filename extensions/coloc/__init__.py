"""Coloc Discord extension — shared coloc-only commands.

Scoped to the coloc server(s). Zunivers-related features live in
:mod:`extensions.zunivers`.
"""

from interactions import Client, Extension, SlashContext, slash_command

from src.core import logging as logutil
from src.core.config import load_config

logger = logutil.init_logger("extensions.coloc")

# No config schema — these commands are scoped to the same server(s) that
# enable Zunivers, but require no per-guild settings of their own.
_, _, _enabled_servers = load_config("moduleZunivers")


class ColocExtension(Extension):
    """Shared coloc commands (fun/meme endpoints)."""

    @slash_command(name="fesse", description="Fesses", scopes=_enabled_servers)
    async def fesse(self, ctx: SlashContext):
        await ctx.send(
            "https://media1.tenor.com/m/YIUbUoKi8ZcAAAAC/sesame-street-kermit-the-frog.gif"
        )

    @slash_command(
        name="massageducul",
        description="Massage du cul",
        scopes=_enabled_servers,
    )
    async def massageducul(self, ctx: SlashContext):
        await ctx.send("https://media1.tenor.com/m/h6OvENNtJh0AAAAC/bebou.gif")


def setup(bot: Client) -> None:
    ColocExtension(bot)


__all__ = ["ColocExtension", "setup"]
