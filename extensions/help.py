"""``/aide`` — what Michel does on this server, module by module (P6, B7), built from what is loaded.

``/aide`` opens on the server's modules: one line each, and a list to pick one
from. A module's page says what it is for, the commands to know and what
happens on its own (the ``__help__`` of its schema, ``ModuleHelp`` in
``src/webui/schemas.py``), then every command of the module. ``/aide
recherche:`` still lists the commands matching a word. When Michel joins a
server it says hello once in the system channel: what it is, ``/aide``, and
the dashboard's address.

Nothing is listed by hand: the command walks ``bot.interactions_by_scope`` for
the server's scope and the global one, so a module switched on or off (which
reloads its extension and re-syncs the server's commands) shows up at once.
Commands are grouped under their module's dashboard label (``HELP_LABEL`` on
an extension without a module), mention-linked when Discord gave them an id,
and those whose default permissions the member lacks are left out, as Discord
hides them from the member's command picker.

Right-click menus (context menus) are listed too, under their module, with
where to find them and the line their decorator gives (``menu_help``,
:mod:`src.discord_ext.menus`).

The command is global but guild-only (``contexts=[GUILD]``, guild install):
it exists on every server the bot is in, and in no DM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from interactions import (
    GLOBAL_SCOPE,
    ActionRow,
    Client,
    CommandType,
    ComponentContext,
    ContextMenu,
    ContextType,
    Embed,
    Extension,
    IntegrationType,
    OptionType,
    Permissions,
    SlashCommand,
    SlashContext,
    StringSelectMenu,
    StringSelectOption,
    component_callback,
    listen,
    slash_command,
    slash_option,
)
from interactions.api.events import GuildJoin

from src.core import clock
from src.core import logging as logutil
from src.core.config import global_config
from src.core.text import EMBED_FIELD_VALUE_LIMIT, clip
from src.discord_ext.look import card
from src.discord_ext.menus import MENU_HELP
from src.discord_ext.messages import guild_id_of, require_guild
from src.discord_ext.paginator import CustomPaginator
from src.webui.schemas import ModuleHelp, module_model

logger = logutil.init_logger(__name__)

OTHER_LABEL = "Autres"
# Room left for the title, description and footer of a page (Discord: 6000 per embed).
PAGE_TEXT_BUDGET = 4500
MAX_FIELDS_PER_PAGE = 25
MAX_SEARCH_LENGTH = 50


MENU_PLACES = {
    CommandType.MESSAGE: "clic droit sur un message → Applications",
    CommandType.USER: "clic droit sur un membre → Applications",
}


@dataclass(frozen=True)
class HelpEntry:
    """One command as ``/aide`` lists it."""

    group: str
    name: str  # "giveaway start"
    mention: str  # "</concours lancer:123>", or "`/concours lancer`" before the first sync
    description: str
    about: ModuleHelp | None = None  # what /aide says about the entry's module


def group_label(command: SlashCommand | ContextMenu) -> str:
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


def group_help(command: SlashCommand | ContextMenu) -> ModuleHelp | None:
    """The page ``/aide`` shows for a command's module: the extension's ``HELP``, else its schema's."""
    extension = getattr(command, "extension", None)
    if extension is None:
        return None
    own = getattr(extension, "HELP", None)
    if isinstance(own, ModuleHelp):
        return own
    for key in getattr(extension, "MODULE_KEYS", ()):
        model = module_model(key)
        if model is not None and model.__help__ is not None:
            return model.__help__
    return None


def _mention(command: SlashCommand, scope: int) -> str:
    cmd_id = command.cmd_id.get(scope)  # type: ignore[call-overload]  # keyed by int scope at runtime
    name = str(command.resolved_name)
    return f"</{name}:{cmd_id}>" if cmd_id else f"`/{name}`"


def _description(command: SlashCommand) -> str:
    text = command.sub_cmd_description if command.is_subcommand else command.description
    return str(text)


def menu_entry(menu: ContextMenu) -> HelpEntry:
    """A right-click menu as ``/aide`` lists it: its name, where it is, what it does."""
    name = str(menu.name)
    place = MENU_PLACES.get(CommandType(menu.type), "clic droit → Applications")
    description = MENU_HELP.get(name, "")
    return HelpEntry(
        group_label(menu),
        name,
        f"**{name}**",
        f"{description} *({place})*" if description else f"*({place})*",
        group_help(menu),
    )


def collect_entries(
    interactions_by_scope: dict[Any, dict[str, Any]],
    guild_id: int,
    can_use: Any,
) -> list[HelpEntry]:
    """The slash commands and right-click menus of *guild_id*, and the global commands.

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
            if isinstance(command, ContextMenu):
                key = f"menu:{command.name}"
                if key not in seen and can_use(command.default_member_permissions):
                    seen.add(key)
                    entries.append(menu_entry(command))
                continue
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
                    group_label(command),
                    name,
                    _mention(command, scope),
                    _description(command),
                    group_help(command),
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
        heading, lines = label, list[str]()
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
    if any(e.mention.startswith("**") for e in entries):
        intro += "\nLes menus en **gras** s'ouvrent d'un clic droit (appui long sur mobile)."
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
            page = card("michel", title="❓ Aide — commandes", description=intro)
            pages.append(page)
            used = 0
        page.add_field(name=heading, value=value, inline=False)
        used += size
    return pages


# The module pages (B7): one list entry per module, 25 per menu, 4 menus at most.
MENU_PREFIX = "aide"
OPTIONS_PER_MENU = 25
MAX_MENUS = 4
# How recent Michel's arrival must be for GuildJoin to mean "just invited": the
# library also sends GuildJoin for every server when the bot starts.
JUST_JOINED = timedelta(hours=1)


def modules(entries: list[HelpEntry]) -> dict[str, list[HelpEntry]]:
    """The entries by module heading, headings sorted on their words."""
    groups: dict[str, list[HelpEntry]] = {}
    for entry in entries:
        groups.setdefault(entry.group, []).append(entry)
    return {label: groups[label] for label in sorted(groups, key=_sort_key)}


def _about(group: list[HelpEntry]) -> ModuleHelp | None:
    return next((e.about for e in group if e.about is not None), None)


def overview_page(groups: dict[str, list[HelpEntry]]) -> Embed:
    """``/aide``'s first page: every module of the server, one line each."""
    lines = [
        f"**{label}** — {about.what}" if (about := _about(group)) else f"**{label}**"
        for label, group in groups.items()
    ]
    intro = (
        "Voici ce que je fais sur ce serveur. Choisis un module dans la liste pour savoir "
        "à quoi il sert, les commandes à connaître et ce qui se passe tout seul.\n"
        "Tu cherches une commande précise ? `/aide recherche:`\n\n"
    )
    return card("michel", title="❓ Aide", description=clip(intro + "\n".join(lines), 4096))


def module_page(label: str, group: list[HelpEntry]) -> Embed:
    """A module's page: what it is for, the commands to know, what it does alone, then the rest."""
    about = _about(group)
    page = card(
        "michel",
        title=label,
        description=about.what if about else None,
        footer="/aide pour revenir à la liste",
    )
    by_name = {e.name: e for e in group}
    if about and about.commands:
        known = []
        for typed in about.commands:
            entry = by_name.get(typed.removeprefix("/"))
            known.append(f"{entry.mention} — {entry.description}" if entry else f"`{typed}`")
        page.add_field(name="🧭 À connaître", value=clip("\n".join(known), 1024), inline=False)
    if about and about.auto:
        page.add_field(name="⚙️ Tout seul", value=clip(about.auto, 1024), inline=False)
    featured = {c.removeprefix("/") for c in about.commands} if about else set()
    rest = [e for e in group if e.name not in featured]
    for heading, value in _fields(rest):
        name = "Les autres commandes" if heading == label else heading.replace(label, "Suite")
        page.add_field(name=name, value=value, inline=False)
    return page


def module_menus(groups: dict[str, list[HelpEntry]], chosen: str | None = None) -> list[ActionRow]:
    """The lists to pick a module from (Discord: 25 options a list)."""
    labels = list(groups)[: OPTIONS_PER_MENU * MAX_MENUS]
    rows = []
    for index in range(0, len(labels), OPTIONS_PER_MENU):
        chunk = labels[index : index + OPTIONS_PER_MENU]
        options = [
            StringSelectOption(
                label=label[:100],
                value=label[:100],
                description=about.what[:100] if (about := _about(groups[label])) else None,
                default=label == chosen,
            )
            for label in chunk
        ]
        placeholder = "Choisis un module"
        if len(labels) > OPTIONS_PER_MENU:
            placeholder += f" ({chunk[0]} … {chunk[-1]})"[:150]
        menu = StringSelectMenu(
            *options,
            custom_id=f"{MENU_PREFIX}:{index // OPTIONS_PER_MENU}",
            placeholder=placeholder[:150],
        )
        rows.append(ActionRow(menu))
    return rows


def hello_card(dashboard_url: str | None) -> Embed:
    """What Michel says once, when a server invites it."""
    text = (
        "Merci pour l'invitation ! Je m'occupe de la vie du serveur : niveaux et classement, "
        "anniversaires, sondages, rappels, musique, modération… Chaque module s'allume "
        "séparément.\n\n"
        "**Membres** : tape `/aide` pour voir ce que je sais faire ici.\n"
    )
    if dashboard_url:
        text += f"**Admins** : tout se règle dans le [tableau de bord]({dashboard_url})."
    else:
        text += "**Admins** : tout se règle dans le tableau de bord du bot."
    return card("michel", title="👋 Salut, moi c'est Michel", description=text)


def just_joined(guild: Any, now: Any) -> bool:
    """Whether Michel arrived in *guild* a moment ago (not a server it was already in)."""
    me = getattr(guild, "me", None)
    joined = getattr(me, "joined_at", None)
    if joined is None:
        return False
    return now - clock.as_utc(joined) <= JUST_JOINED


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
    HELP = ModuleHelp(
        "Ce que Michel fait sur ce serveur, module par module.",
        ("/aide",),
        "Quand Michel arrive sur un serveur, il se présente dans le salon système.",
    )

    def __init__(self, bot: Client):
        self.bot: Client = bot

    def _entries(self, ctx: Any) -> list[HelpEntry]:
        return collect_entries(
            self.bot.interactions_by_scope, int(guild_id_of(ctx)), _member_can_use(ctx.author)
        )

    @slash_command(
        name="aide",
        description="Ce que Michel fait sur ce serveur, module par module",
        contexts=[ContextType.GUILD],
        integration_types=[IntegrationType.GUILD_INSTALL],
    )
    @slash_option(
        "recherche",
        "Mot à chercher dans les commandes (ex : concours, rappel)",
        opt_type=OptionType.STRING,
        required=False,
        max_length=MAX_SEARCH_LENGTH,
        argument_name="search",
    )
    async def aide(self, ctx: SlashContext, search: str | None = None) -> None:
        if not await require_guild(ctx):
            return
        entries = matching(self._entries(ctx), search)
        if not entries:
            await ctx.send(
                f"Aucune commande ne correspond à « {search} ». Essaie un autre mot, "
                "ou `/aide` tout court pour la liste des modules."
                if search
                else "Aucune commande disponible ici.",
                ephemeral=True,
            )
            return
        if not search:
            groups = modules(entries)
            await ctx.send(
                embeds=[overview_page(groups)], components=module_menus(groups), ephemeral=True
            )
            return
        pages = render_pages(entries, search)
        if len(pages) == 1:
            await ctx.send(embeds=pages, ephemeral=True)
            return
        paginator = CustomPaginator.create_from_embeds(self.bot, *pages, timeout=600)
        await paginator.send(ctx, ephemeral=True)

    @component_callback(re.compile(rf"^{MENU_PREFIX}:\d+$"))
    async def on_module_chosen(self, ctx: ComponentContext) -> None:
        """A module picked in ``/aide``'s list: its page, the lists kept to pick another."""
        groups = modules(self._entries(ctx))
        chosen = (ctx.values or [""])[0]
        if chosen not in groups:  # switched off since, or not for this member
            await ctx.edit_origin(embeds=[overview_page(groups)], components=module_menus(groups))
            return
        await ctx.edit_origin(
            embeds=[module_page(chosen, groups[chosen])],
            components=module_menus(groups, chosen),
        )

    @listen(GuildJoin)
    async def on_guild_join(self, event: GuildJoin) -> None:
        """Say hello once, in the system channel of a server that just invited Michel."""
        if not self.bot.is_ready:
            return  # the startup replays every server the bot is in
        guild = self.bot.get_guild(event.guild_id)
        if guild is None or not just_joined(guild, clock.now()):
            return
        channel = getattr(guild, "system_channel", None)
        me = getattr(guild, "me", None)
        if channel is None or me is None:
            logger.info("Guild %s joined: no system channel to say hello in", guild.id)
            return
        needed = Permissions.VIEW_CHANNEL | Permissions.SEND_MESSAGES | Permissions.EMBED_LINKS
        if channel.permissions_for(me) & needed != needed:
            logger.info("Guild %s joined: no right to say hello in its system channel", guild.id)
            return
        webui = global_config().get("webui") or {}
        base = str(webui.get("baseUrl") or "").strip() if webui.get("enabled") else ""
        url = base or None
        try:
            await channel.send(embeds=[hello_card(url)])
        except Exception as e:
            logger.warning("Guild %s joined: hello not posted: %s", guild.id, e)


def setup(bot: Client) -> None:
    HelpExtension(bot)


__all__ = [
    "HelpExtension",
    "collect_entries",
    "hello_card",
    "just_joined",
    "module_menus",
    "module_page",
    "modules",
    "overview_page",
    "render_pages",
    "setup",
]
