"""``/aide`` — the commands a member can use on this server, built from what is loaded.

Nothing is listed by hand: the command walks ``bot.interactions_by_scope`` for
the server's scope and the global one, so a module switched on or off (which
reloads its extension and re-syncs the server's commands) shows up at once.
Commands are grouped under their module's dashboard label (``HELP_LABEL`` on
an extension without a module), mention-linked when Discord gave them an id,
and those whose default permissions the member lacks are left out, as Discord
hides them from the member's command picker.

The command is global but guild-only (``contexts=[GUILD]``, guild install):
it exists on every server the bot is in, and in no DM.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from interactions import (
    GLOBAL_SCOPE,
    Client,
    ContextType,
    Embed,
    Extension,
    IntegrationType,
    OptionType,
    SlashCommand,
    SlashContext,
    slash_command,
    slash_option,
)

from src.core import logging as logutil
from src.core.text import EMBED_FIELD_VALUE_LIMIT, clip
from src.discord_ext.embeds import Colors
from src.discord_ext.messages import require_guild
from src.discord_ext.paginator import CustomPaginator
from src.webui.schemas import module_model

logger = logutil.init_logger(__name__)

OTHER_LABEL = "Autres"
# Room left for the title, description and footer of a page (Discord: 6000 per embed).
PAGE_TEXT_BUDGET = 4500
MAX_FIELDS_PER_PAGE = 25
MAX_SEARCH_LENGTH = 50


@dataclass(frozen=True)
class HelpEntry:
    """One command as ``/aide`` lists it."""

    group: str
    name: str  # "giveaway start"
    mention: str  # "</giveaway start:123>", or "`/giveaway start`" before the first sync
    description: str


def group_label(command: SlashCommand) -> str:
    """The heading a command is listed under: its module's dashboard label."""
    extension = getattr(command, "extension", None)
    if extension is None:
        return OTHER_LABEL
    label = getattr(extension, "HELP_LABEL", None)
    if isinstance(label, str) and label:
        return label
    for key in getattr(extension, "MODULE_KEYS", ()):
        model = module_model(key)
        if model is not None and model.__label__:
            return f"{model.__icon__} {model.__label__}".strip()
    return type(extension).__name__.removesuffix("Extension") or OTHER_LABEL


def _mention(command: SlashCommand, scope: int) -> str:
    cmd_id = command.cmd_id.get(scope)
    name = str(command.resolved_name)
    return f"</{name}:{cmd_id}>" if cmd_id else f"`/{name}`"


def _description(command: SlashCommand) -> str:
    text = command.sub_cmd_description if command.is_subcommand else command.description
    return str(text)


def collect_entries(
    interactions_by_scope: dict[Any, dict[str, Any]],
    guild_id: int,
    can_use: Any,
) -> list[HelpEntry]:
    """The slash commands of *guild_id* and the global ones usable in a server.

    *can_use* is called with a command's default member permissions (``None``
    when it has none) and says whether the member has them. Discord applies
    the permissions of a command's first subcommand to all of them, so the
    first one met decides for its whole base command, as in the sync payload.
    """
    entries: list[HelpEntry] = []
    seen: set[str] = set()
    for scope in (int(guild_id), GLOBAL_SCOPE):
        base_permissions: dict[str, Any] = {}
        for command in interactions_by_scope.get(scope, {}).values():
            if not isinstance(command, SlashCommand):
                continue
            base = str(command.name)
            base_permissions.setdefault(base, command.default_member_permissions)
            if scope == GLOBAL_SCOPE and ContextType.GUILD not in (command.contexts or []):
                continue
            name = str(command.resolved_name)
            if name in seen or not can_use(base_permissions[base]):
                continue
            seen.add(name)
            entries.append(
                HelpEntry(
                    group_label(command), name, _mention(command, scope), _description(command)
                )
            )
    return entries


def matching(entries: list[HelpEntry], search: str | None) -> list[HelpEntry]:
    """The entries whose name, description or group contains *search* (all without one)."""
    if not search:
        return entries
    needle = search.strip().lower()
    return [
        e
        for e in entries
        if needle in e.name.lower() or needle in e.description.lower() or needle in e.group.lower()
    ]


def _sort_key(label: str) -> str:
    """Sort headings on their words, not on the emoji in front."""
    return "".join(ch for ch in label if ch.isalnum() or ch.isspace()).strip().lower()


def _fields(entries: list[HelpEntry]) -> list[tuple[str, str]]:
    """(heading, lines) fields, a group split over several when it outgrows a field."""
    groups: dict[str, list[HelpEntry]] = {}
    for entry in entries:
        groups.setdefault(entry.group, []).append(entry)
    fields: list[tuple[str, str]] = []
    for label in sorted(groups, key=_sort_key):
        heading, lines = label, []
        for entry in sorted(groups[label], key=lambda e: e.name):
            line = clip(f"{entry.mention} — {entry.description}", EMBED_FIELD_VALUE_LIMIT)
            if lines and len("\n".join([*lines, line])) > EMBED_FIELD_VALUE_LIMIT:
                fields.append((heading, "\n".join(lines)))
                heading, lines = f"{label} (suite)", []
            lines.append(line)
        fields.append((heading, "\n".join(lines)))
    return fields


def render_pages(entries: list[HelpEntry], search: str | None = None) -> list[Embed]:
    """The help embeds: every group's commands, spread over pages Discord accepts."""
    count = len(entries)
    intro = f"**{count}** commande{'s' if count > 1 else ''} disponible{'s' if count > 1 else ''}"
    intro += f" pour « {search} »." if search else " ici."
    intro += "\nClique sur une commande pour la préparer dans la barre de saisie."
    pages: list[Embed] = []
    page: Embed | None = None
    used = 0
    for heading, value in _fields(entries):
        size = len(heading) + len(value)
        if (
            page is None
            or len(page.fields) >= MAX_FIELDS_PER_PAGE
            or used + size > PAGE_TEXT_BUDGET
        ):
            page = Embed(title="❓ Aide — commandes", description=intro, color=Colors.UTIL)
            pages.append(page)
            used = 0
        page.add_field(name=heading, value=value, inline=False)
        used += size
    return pages


def _member_can_use(member: Any) -> Any:
    def can_use(required: Any) -> bool:
        if not required:
            return True
        has_permission = getattr(member, "has_permission", None)
        return bool(callable(has_permission) and has_permission(required))

    return can_use


class HelpExtension(Extension):
    MODULE_KEYS = ()
    HELP_LABEL = "❓ Aide"

    def __init__(self, bot: Client):
        self.bot: Client = bot

    @slash_command(
        name="aide",
        description="Lister les commandes disponibles sur ce serveur",
        contexts=[ContextType.GUILD],
        integration_types=[IntegrationType.GUILD_INSTALL],
    )
    @slash_option(
        "recherche",
        "Mot à chercher dans les commandes (ex : giveaway, rappel)",
        opt_type=OptionType.STRING,
        required=False,
        max_length=MAX_SEARCH_LENGTH,
        argument_name="search",
    )
    async def aide(self, ctx: SlashContext, search: str | None = None) -> None:
        if not await require_guild(ctx):
            return
        entries = matching(
            collect_entries(
                self.bot.interactions_by_scope, int(ctx.guild_id), _member_can_use(ctx.author)
            ),
            search,
        )
        if not entries:
            await ctx.send(
                f"Aucune commande ne correspond à « {search} »."
                if search
                else "Aucune commande disponible ici.",
                ephemeral=True,
            )
            return
        pages = render_pages(entries, search)
        if len(pages) == 1:
            await ctx.send(embeds=pages, ephemeral=True)
            return
        paginator = CustomPaginator.create_from_embeds(self.bot, *pages, timeout=600)
        await paginator.send(ctx, ephemeral=True)


def setup(bot: Client) -> None:
    HelpExtension(bot)


__all__ = ["HelpExtension", "collect_entries", "render_pages", "setup"]
