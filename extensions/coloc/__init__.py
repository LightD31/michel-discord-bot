"""Coloc Discord extension — shared coloc-only commands.

Scoped to the coloc server(s). Zunivers-related features live in
:mod:`extensions.zunivers`.
"""

from interactions import Client, Extension, SlashContext, slash_command

from src.core import logging as logutil
from src.core.config import load_config

logger = logutil.init_logger("extensions.coloc")

# No config schema of its own — these commands are scoped to the same
# server(s) that enable Zunivers and read their GIF URLs from that module's
# config (``colocFesseGifUrl`` / ``colocMassageGifUrl``).
_, _module_config, _enabled_servers = load_config("moduleZunivers")
_coloc_config = _module_config.get(_enabled_servers[0], {}) if _enabled_servers else {}


class ColocExtension(Extension):
    """Shared coloc commands (fun/meme endpoints)."""

    @slash_command(name="fesse", description="Fesses", scopes=_enabled_servers)
    async def fesse(self, ctx: SlashContext):
        await self._send_gif(ctx, "colocFesseGifUrl")

    @slash_command(
        name="massageducul",
        description="Massage du cul",
        scopes=_enabled_servers,
    )
    async def massageducul(self, ctx: SlashContext):
        await self._send_gif(ctx, "colocMassageGifUrl")

    @staticmethod
    async def _send_gif(ctx: SlashContext, config_key: str) -> None:
        """Send the GIF configured under *config_key*, or say it is missing.

        The URLs live in the Web UI (module Zunivers) rather than in the code.
        """
        url = _coloc_config.get(config_key) or ""
        if not url:
            logger.warning("Coloc: aucun GIF configuré pour %s", config_key)
            await ctx.send(
                "Aucun GIF configuré pour cette commande (dashboard → Zunivers).",
                ephemeral=True,
            )
            return
        await ctx.send(url)


def setup(bot: Client) -> None:
    ColocExtension(bot)


__all__ = ["ColocExtension", "setup"]
