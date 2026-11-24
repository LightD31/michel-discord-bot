"""Custom slash commands — one set per Discord server.

Each guild declares its own commands from the dashboard (module
"Commandes personnalisées"): a name, a description, and one or more responses
— a GIF link or a plain text — one of which is picked at random on every use.
A command may aim at a member (a "cible" option), wait between two uses by
one member, and be kept to some roles (A9).
Nothing is hardcoded here; the extension only turns that config into real
guild-scoped slash commands.

Domain logic (parsing, validation, the random pick) lives in
``features/customcommands``.
"""

from typing import Any

from interactions import (
    GLOBAL_SCOPE,
    Client,
    Extension,
    OptionType,
    SlashCommand,
    SlashCommandOption,
    SlashContext,
    to_snowflake,
)

from extensions.customcommands._common import CustomCommandsConfig, logger
from features.customcommands import (
    MODULE_KEY,
    Cooldowns,
    CustomCommand,
    parse_commands,
    pick_response,
    render_response,
)
from features.links import shorten_text
from src.core.config import ModuleConfig
from src.discord_ext.look import card, oops
from src.discord_ext.mentions import NO_PINGS, mentions_written_in
from src.discord_ext.messages import guild_id_of, guild_of
from src.discord_ext.scopes import guild_scopes

CUSTOM_COMMANDS = ModuleConfig(MODULE_KEY)
TARGET_OPTION = "cible"
# Shared by every instance: a dashboard save reloads the extension, and a
# member shouldn't get a fresh cooldown out of it.
COOLDOWNS = Cooldowns()


class CustomCommandsExtension(Extension):
    """Registers each guild's configured commands as real slash commands."""

    MODULE_KEYS = ("moduleCustomCommands",)

    # The commands themselves are built from the config.

    RELOAD_ON_SAVE = True

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
        for guild_id, guild_config in CUSTOM_COMMANDS.items():
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
        options: list[SlashCommandOption | dict] = []
        if command.target != "none":
            options.append(
                SlashCommandOption(
                    name=TARGET_OPTION,
                    description="Le membre que tu vises",
                    type=OptionType.USER,
                    required=command.target == "required",
                )
            )
        slash = SlashCommand(
            name=command.name,
            description=command.description,
            scopes=guild_scopes([guild_id]),
            options=options,
            callback=_build_callback(command),
        )
        slash.extension = self
        self.bot.add_interaction(slash)
        # Extension.drop() walks this list to unregister commands, so a reload
        # (or a disabled module) removes what this instance registered.
        self._commands.append(slash)


def _refusal(command: CustomCommand, ctx: SlashContext, guild_id: str) -> str | None:
    """Why the author may not run *command* now, in French, or ``None``."""
    if command.roles:
        held = {str(getattr(r, "id", r)) for r in getattr(ctx.author, "roles", [])}
        if not held & set(command.roles):
            names = ", ".join(f"<@&{rid}>" for rid in command.roles)
            return oops(f"/{command.name} est réservée à : {names}.", icon="🔒")
    wait = COOLDOWNS.remaining(guild_id, command.name, str(ctx.author.id), command.cooldown)
    if wait:
        return oops(f"Encore {wait} s avant de relancer /{command.name}.", icon="⏱️")
    return None


def _build_callback(command: CustomCommand):
    """Build the coroutine answering *command* with one of its responses."""

    # The option is a named parameter: interactions.py passes options only to
    # the parameters it finds (a bare ``**kwargs`` gets none of them).
    async def respond(ctx: SlashContext, cible: Any = None) -> None:
        guild_id = str(guild_id_of(ctx))
        refusal = _refusal(command, ctx, guild_id)
        if refusal:
            await ctx.send(refusal, ephemeral=True, allowed_mentions=NO_PINGS)
            return
        COOLDOWNS.use(guild_id, command.name, str(ctx.author.id), command.cooldown)
        target = cible
        template = pick_response(command)
        response = render_response(
            template,
            author=ctx.author.mention,
            target=target.mention if target is not None else None,
            server=str(getattr(guild_of(ctx), "name", "") or ""),
        )
        # What the admins wrote may ping what it names (@everyone included);
        # the member aimed at is pinged, never the one who ran it.
        allowed = mentions_written_in(template)
        if target is not None and int(target.id) != int(ctx.author.id):
            allowed.users = [*(allowed.users or []), int(target.id)]
        embeds = []
        if response.image_url:
            embed = card(MODULE_KEY, author=False)
            embed.set_image(url=response.image_url)
            embeds.append(embed)
        content = (
            await shorten_text(response.text, guild_id=guild_id_of(ctx)) if response.text else None
        )
        await ctx.send(content, embeds=embeds, allowed_mentions=allowed)

    return respond


def setup(bot: Client) -> None:
    CustomCommandsExtension(bot)


__all__ = ["CustomCommandsConfig", "CustomCommandsExtension", "setup"]
