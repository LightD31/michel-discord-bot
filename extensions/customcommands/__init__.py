"""Custom slash commands — one set per Discord server.

Each guild declares its own commands from the dashboard (module
"Commandes personnalisées"): a name, a description, and one or more responses
— a GIF link or a plain text — one of which is picked at random on every use.
Nothing is hardcoded here; the extension only turns that config into real
guild-scoped slash commands.

Domain logic (parsing, validation, the random pick) lives in
``features/customcommands``.
"""

from interactions import (
    GLOBAL_SCOPE,
    Client,
    Extension,
    SlashCommand,
    SlashContext,
    to_snowflake,
)

from extensions.customcommands._common import CustomCommandsConfig, logger
from features.customcommands import CustomCommand, parse_commands, pick_response
from features.links import shorten_text
from src.core.config import load_config


class CustomCommandsExtension(Extension):
    """Registers each guild's configured commands as real slash commands."""

    def __init__(self, bot: Client) -> None:
        # At startup every extension is loaded before login, so registering
        # here could claim a name another extension has not declared yet.
        # ``async_start`` runs once all of them are loaded and before the first
        # command sync, which is exactly what we want. It never fires again on
        # a live reload (a dashboard save), so in that case — the bot is
        # already ready — register straight away instead.
        if bot.is_ready:
            self._register_all()

    async def async_start(self) -> None:
        """Startup hook: register after every other extension has loaded."""
        self._register_all()

    # ── Registration ────────────────────────────────────────────────

    def _register_all(self) -> None:
        _, module_config, enabled_servers = load_config("moduleCustomCommands")
        for guild_id in enabled_servers:
            guild_config = module_config.get(guild_id) or {}
            parsed = parse_commands(
                guild_config.get("commands"),
                reserved_names=self._reserved_names(guild_id),
            )
            for error in parsed.errors:
                logger.warning("Serveur %s — %s", guild_id, error)
            for command in parsed.commands:
                self._register(guild_id, command)
            if parsed.commands:
                logger.info(
                    "Serveur %s : %d commande(s) personnalisée(s) enregistrée(s) (%s)",
                    guild_id,
                    len(parsed.commands),
                    ", ".join(f"/{c.name}" for c in parsed.commands),
                )

    def _reserved_names(self, guild_id: str) -> set[str]:
        """Command names already taken by the bot in this guild's scope."""
        by_scope = self.bot.interactions_by_scope
        return set(by_scope.get(GLOBAL_SCOPE, {})) | set(by_scope.get(to_snowflake(guild_id), {}))

    def _register(self, guild_id: str, command: CustomCommand) -> None:
        slash = SlashCommand(
            name=command.name,
            description=command.description,
            scopes=[guild_id],
            callback=_build_callback(command),
        )
        slash.extension = self
        self.bot.add_interaction(slash)
        # Extension.drop() walks this list to unregister commands, so a reload
        # (or a disabled module) removes what this instance registered.
        self._commands.append(slash)


def _build_callback(command: CustomCommand):
    """Build the coroutine answering *command* with one of its responses."""

    async def respond(ctx: SlashContext) -> None:
        await ctx.send(await shorten_text(pick_response(command)))

    return respond


def setup(bot: Client) -> None:
    CustomCommandsExtension(bot)


__all__ = ["CustomCommandsConfig", "CustomCommandsExtension", "setup"]
