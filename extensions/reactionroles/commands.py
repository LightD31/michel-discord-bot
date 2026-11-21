"""Slash commands for managing role menus.

``/menu-roles creer salon: titre:`` answers with an ephemeral builder: a role
picker (Discord's own), the menu's preview and "Publier" (B3). Each role is
checked as the dashboard builder checks it (staff roles refused, below the
author's highest role and the bot's).
"""

import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from interactions import (
    ActionRow,
    BaseChannel,
    Button,
    ButtonStyle,
    ChannelType,
    ComponentContext,
    OptionType,
    Permissions,
    RoleSelectMenu,
    SlashCommandChoice,
    SlashContext,
    component_callback,
    slash_command,
    slash_default_member_permission,
    slash_option,
)

from features.reactionroles import RoleMenu, RoleMenuEntry
from src.core import clock
from src.discord_ext.channels import fetch_guild_channel
from src.discord_ext.errors import failure_reply
from src.discord_ext.look import card, ok, oops
from src.discord_ext.messages import (
    CAN_SEND,
    author_can,
    edit_message_if_changed,
    require_guild,
    send_error,
    send_success,
)
from src.discord_ext.rolemenus import (
    bot_top_position,
    entries_refusal,
    menu_author,
    render_menu,
)
from src.discord_ext.scopes import guild_scopes

from ._common import MAX_ENTRIES, REACTION_ROLES, build_embed, logger

# A menu being built: who builds it, where it goes, and the roles picked so far.
# In memory: an ephemeral draft is lost on a restart, as its message is.
DRAFT_PREFIX = "rrdraft"  # rrdraft:{draft_id}:{roles|publish|cancel}
_DRAFT_RE = re.compile(rf"^{DRAFT_PREFIX}:([0-9a-f]{{12}}):(roles|publish|cancel)$")
DRAFT_LIFETIME = timedelta(minutes=15)


@dataclass
class Draft:
    guild_id: str
    channel_id: str
    author_id: str
    title: str
    description: str | None
    style: str
    max_roles: int | None
    created_at: datetime
    entries: list[RoleMenuEntry] = field(default_factory=list)


DRAFTS: dict[str, Draft] = {}


def _forget_old_drafts(now: datetime) -> None:
    for key in [k for k, d in DRAFTS.items() if now - d.created_at > DRAFT_LIFETIME]:
        del DRAFTS[key]


def entries_from_roles(roles: list[Any]) -> list[RoleMenuEntry]:
    """The menu's entries for the roles picked in Discord: their name and emoji."""
    return [
        RoleMenuEntry(
            role_id=str(role.id),
            emoji=str(getattr(role, "unicode_emoji", None) or ""),
            label=str(getattr(role, "name", role.id))[:80],
        )
        for role in roles
    ]


def draft_view(draft_id: str, draft: Draft, problem: str | None = None) -> dict[str, Any]:
    """The ephemeral builder: what to do, the menu's preview, the role picker, Publier."""
    if problem:
        content = oops(problem)
    elif draft.entries:
        content = "Voici l'aperçu. Change les rôles si besoin, puis **Publier**."
    else:
        content = (
            f"Choisis les rôles du menu ({MAX_ENTRIES} au plus) : l'aperçu s'affiche ici, "
            f"puis **Publier** l'envoie dans <#{draft.channel_id}>."
        )
    preview = build_embed(
        draft.title,
        draft.description,
        draft.entries,
        max_roles=draft.max_roles,
    )
    ready = bool(draft.entries) and problem is None
    return {
        "content": content,
        "embeds": [preview],
        "components": [
            ActionRow(
                RoleSelectMenu(
                    placeholder="Les rôles du menu",
                    min_values=1,
                    max_values=MAX_ENTRIES,
                    custom_id=f"{DRAFT_PREFIX}:{draft_id}:roles",
                )
            ),
            ActionRow(
                Button(
                    label="Publier",
                    style=ButtonStyle.SUCCESS,
                    custom_id=f"{DRAFT_PREFIX}:{draft_id}:publish",
                    disabled=not ready,
                ),
                Button(
                    label="Annuler",
                    style=ButtonStyle.SECONDARY,
                    custom_id=f"{DRAFT_PREFIX}:{draft_id}:cancel",
                ),
            ),
        ],
    }


async def _can_manage_menus(ctx: SlashContext) -> bool:
    """Whether the author may delete or edit a menu, else tell them.

    The default permission is only a default: a server can open the commands
    to everyone. Creating a menu checks each role against its author
    (``entries_refusal``); deleting or editing one hands out nothing, so
    « Gérer les rôles » is enough, as in Discord.
    """
    if ctx.author.has_permission(Permissions.MANAGE_ROLES):
        return True
    await send_error(ctx, "Il faut la permission « Gérer les rôles ».")
    return False


class CommandsMixin:
    """Admin slash commands to create / list / edit / delete role menus."""

    @slash_command(
        name="menu-roles",
        description="Gérer les menus de rôles",
        sub_cmd_name="creer",
        sub_cmd_description="Créer un menu de rôles : tu choisis les rôles, tu vois l'aperçu",
        scopes=guild_scopes(REACTION_ROLES.guild_ids()),
    )
    @slash_option(
        "salon",
        "Salon où publier le menu",
        opt_type=OptionType.CHANNEL,
        required=True,
        channel_types=[ChannelType.GUILD_TEXT, ChannelType.GUILD_NEWS],
        argument_name="channel",
    )
    @slash_option(
        "titre",
        "Titre du menu",
        opt_type=OptionType.STRING,
        required=True,
        max_length=256,
        argument_name="title",
    )
    @slash_option(
        "description",
        "Description optionnelle affichée au-dessus des rôles",
        opt_type=OptionType.STRING,
        required=False,
        max_length=1000,
    )
    @slash_option(
        "style",
        "Un bouton par rôle (défaut) ou une liste déroulante",
        opt_type=OptionType.STRING,
        required=False,
        choices=[
            SlashCommandChoice(name="Boutons", value="buttons"),
            SlashCommandChoice(name="Liste déroulante", value="select"),
        ],
    )
    @slash_option(
        "limite",
        "Combien de rôles du menu un membre garde à la fois (1 : un seul, ex. les couleurs)",
        opt_type=OptionType.INTEGER,
        required=False,
        min_value=1,
        max_value=MAX_ENTRIES,
        argument_name="max_roles",
    )
    @slash_default_member_permission(Permissions.MANAGE_ROLES)
    async def rolemenu_create(
        self,
        ctx: SlashContext,
        channel: BaseChannel,
        title: str,
        description: str | None = None,
        style: str = "buttons",
        max_roles: int | None = None,
    ) -> None:
        if not await require_guild(ctx):
            return
        if not hasattr(channel, "send"):
            await send_error(ctx, "Ce salon ne permet pas l'envoi de messages.")
            return
        # The bot posts with its own rights: the author must be able to post there.
        if not await author_can(ctx, channel, CAN_SEND):
            return
        now = clock.now()
        _forget_old_drafts(now)
        draft_id = secrets.token_hex(6)
        DRAFTS[draft_id] = Draft(
            guild_id=str(ctx.guild_id),
            channel_id=str(channel.id),
            author_id=str(ctx.author.id),
            title=title,
            description=description,
            style=style if style in ("buttons", "select") else "buttons",
            max_roles=max_roles,
            created_at=now,
        )
        await ctx.send(**draft_view(draft_id, DRAFTS[draft_id]), ephemeral=True)

    @component_callback(_DRAFT_RE)
    async def on_draft(self, ctx: ComponentContext) -> None:
        match = _DRAFT_RE.match(ctx.custom_id)
        if not match:
            return
        draft_id, action = match.groups()
        draft = DRAFTS.get(draft_id)
        if draft is None or clock.now() - draft.created_at > DRAFT_LIFETIME:
            DRAFTS.pop(draft_id, None)
            await ctx.edit_origin(
                content="⏱️ Ce brouillon a expiré : relance `/menu-roles creer`.",
                embeds=[],
                components=[],
            )
            return
        if draft.author_id != str(ctx.author.id):
            await send_error(ctx, "Ce brouillon n'est pas le tien.", icon="🔒")
            return
        if action == "cancel":
            DRAFTS.pop(draft_id, None)
            await ctx.edit_origin(content="Menu abandonné.", embeds=[], components=[])
            return
        if action == "roles":
            draft.entries = entries_from_roles(list(ctx.values or []))
            problem = await self._entries_problem(ctx, draft.entries)
            await ctx.edit_origin(**draft_view(draft_id, draft, problem))
            return
        await self._publish(ctx, draft_id, draft)

    async def _entries_problem(self, ctx: Any, entries: list[RoleMenuEntry]) -> str | None:
        """Why these roles can't go in a menu by this author (staff roles, hierarchy)."""
        return entries_refusal(
            ctx.guild,
            entries,
            author=menu_author(ctx.guild, ctx.author),
            bot_position=await bot_top_position(self.bot, ctx.guild),
        )

    async def _publish(self, ctx: ComponentContext, draft_id: str, draft: Draft) -> None:
        """Post the menu the draft describes, the rules checked once more."""
        problem = await self._entries_problem(ctx, draft.entries) if draft.entries else None
        if not draft.entries or problem:
            await ctx.edit_origin(**draft_view(draft_id, draft, problem or "Choisis un rôle."))
            return
        channel = await fetch_guild_channel(self.bot, draft.guild_id, draft.channel_id)
        if channel is None or not hasattr(channel, "send"):
            await send_error(ctx, "Ce salon n'existe plus.")
            return
        if not await author_can(ctx, channel, CAN_SEND):
            return
        menu = RoleMenu(
            guild_id=draft.guild_id,
            channel_id=draft.channel_id,
            title=draft.title,
            description=draft.description,
            entries=draft.entries,
            style=draft.style,  # type: ignore[arg-type]
            max_roles=draft.max_roles,
            created_by=draft.author_id,
            created_at=clock.now(),
        )
        repo = self.repository(draft.guild_id)
        menu_id = await repo.add(menu)
        try:
            sent = await channel.send(**render_menu(menu, menu_id))  # type: ignore[union-attr]
        except Exception as e:
            await repo.delete(menu_id)
            await send_error(ctx, failure_reply("Je n'ai pas pu publier le menu", e, logger))
            return
        await repo.update(menu_id, message_id=str(sent.id))
        DRAFTS.pop(draft_id, None)
        logger.info(
            "Role menu %s created by %s in #%s with %d entries (%s)",
            menu_id,
            ctx.author.username,
            getattr(channel, "name", channel.id),
            len(draft.entries),
            draft.style,
        )
        await ctx.edit_origin(
            content=ok(f"Menu publié dans <#{draft.channel_id}>."), embeds=[], components=[]
        )

    @rolemenu_create.subcommand(
        sub_cmd_name="liste",
        sub_cmd_description="Lister les menus de rôles de ce serveur",
    )
    @slash_default_member_permission(Permissions.MANAGE_ROLES)
    async def rolemenu_list(self, ctx: SlashContext) -> None:
        if not await require_guild(ctx):
            return
        menus = await self.repository(ctx.guild_id).list()
        if not menus:
            await ctx.send("Aucun menu de rôles enregistré.", ephemeral=True)
            return

        lines = []
        for menu in menus:
            link = (
                f"https://discord.com/channels/{menu.guild_id}/{menu.channel_id}/{menu.message_id}"
                if menu.message_id
                else "—"
            )
            lines.append(
                f"`{menu.id}` — **{menu.title}** ({len(menu.entries)} rôles) "
                f"<#{menu.channel_id}> [↗]({link})"
            )
        embed = card("moduleReactionRoles", title="Menus de rôles", description="\n".join(lines))
        await ctx.send(embeds=[embed], ephemeral=True)

    @rolemenu_create.subcommand(
        sub_cmd_name="supprimer",
        sub_cmd_description="Supprimer un menu de rôles",
    )
    @slash_option(
        "menu",
        "ID du menu (visible via /menu-roles liste)",
        opt_type=OptionType.STRING,
        required=True,
        argument_name="menu_id",
    )
    @slash_default_member_permission(Permissions.MANAGE_ROLES)
    async def rolemenu_delete(self, ctx: SlashContext, menu_id: str) -> None:
        if not await require_guild(ctx) or not await _can_manage_menus(ctx):
            return
        menu = await self.repository(ctx.guild_id).get(menu_id)
        if not menu:
            await send_error(ctx, "Menu introuvable.")
            return

        await self.repository(ctx.guild_id).delete(menu_id)

        if menu.message_id:
            try:
                channel = await fetch_guild_channel(self.bot, menu.guild_id, menu.channel_id)
                if channel and hasattr(channel, "fetch_message"):
                    msg = await channel.fetch_message(int(menu.message_id))
                    if msg:
                        await msg.delete()
            except Exception as e:
                logger.warning("Could not delete role menu message %s: %s", menu.message_id, e)

        logger.info("Role menu %s deleted by %s", menu_id, ctx.author.username)
        await send_success(ctx, "Menu supprimé.")

    @rolemenu_create.subcommand(
        sub_cmd_name="modifier",
        sub_cmd_description="Modifier le titre ou la description d'un menu existant",
    )
    @slash_option(
        "menu",
        "ID du menu (visible via /menu-roles liste)",
        opt_type=OptionType.STRING,
        required=True,
        argument_name="menu_id",
    )
    @slash_option(
        "titre",
        "Nouveau titre (optionnel)",
        opt_type=OptionType.STRING,
        required=False,
        argument_name="title",
    )
    @slash_option(
        "description",
        "Nouvelle description (optionnel, laisser vide pour conserver)",
        opt_type=OptionType.STRING,
        required=False,
    )
    @slash_default_member_permission(Permissions.MANAGE_ROLES)
    async def rolemenu_edit(
        self,
        ctx: SlashContext,
        menu_id: str,
        title: str | None = None,
        description: str | None = None,
    ) -> None:
        if not await require_guild(ctx) or not await _can_manage_menus(ctx):
            return
        if title is None and description is None:
            await send_error(ctx, "Indique au moins un titre ou une description à modifier.")
            return

        menu = await self.repository(ctx.guild_id).get(menu_id)
        if not menu:
            await send_error(ctx, "Menu introuvable.")
            return

        new_title = title if title is not None else menu.title
        new_description = description if description is not None else menu.description
        await self.repository(ctx.guild_id).update(
            menu_id, title=new_title, description=new_description
        )

        if menu.message_id:
            try:
                channel = await fetch_guild_channel(self.bot, menu.guild_id, menu.channel_id)
                if channel and hasattr(channel, "fetch_message"):
                    msg = await channel.fetch_message(int(menu.message_id))
                    if msg:
                        edited = menu.model_copy(
                            update={"title": new_title, "description": new_description}
                        )
                        await edit_message_if_changed(msg, **render_menu(edited), logger=logger)
            except Exception as e:
                logger.warning("Could not edit role menu message %s: %s", menu.message_id, e)

        await send_success(ctx, "Menu modifié.")
