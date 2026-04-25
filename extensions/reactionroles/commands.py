"""Slash commands for managing role menus."""

from datetime import datetime

from interactions import (
    ActionRow,
    BaseChannel,
    Button,
    ButtonStyle,
    ChannelType,
    Embed,
    OptionType,
    Permissions,
    SlashContext,
    slash_command,
    slash_default_member_permission,
    slash_option,
)

from features.reactionroles import RoleMenu, RoleMenuEntry
from src.discord_ext.embeds import Colors
from src.discord_ext.messages import require_guild, send_error, send_success

from ._common import BUTTON_PREFIX, MAX_ENTRIES, enabled_servers_int, logger


def _parse_entries(raw: str) -> list[RoleMenuEntry] | str:
    """Parse ``"roleId,emoji,label;roleId,emoji,label"``. Return list or error message."""
    parts = [p.strip() for p in raw.split(";") if p.strip()]
    if not parts:
        return "Vous devez fournir au moins une entrée."
    if len(parts) > MAX_ENTRIES:
        return f"Maximum {MAX_ENTRIES} entrées par menu (Discord limite à 5×5 boutons)."

    entries: list[RoleMenuEntry] = []
    for idx, part in enumerate(parts, 1):
        bits = [b.strip() for b in part.split(",", 2)]
        if len(bits) != 3:
            return f"Entrée {idx} invalide : attendu « roleId,emoji,label »."
        role_id, emoji, label = bits
        if not role_id.isdigit():
            return f"Entrée {idx} : roleId doit être un identifiant numérique."
        if not emoji:
            return f"Entrée {idx} : emoji manquant."
        if not label:
            return f"Entrée {idx} : libellé manquant."
        if len(label) > 80:
            return f"Entrée {idx} : libellé trop long (max 80 caractères)."
        entries.append(RoleMenuEntry(role_id=role_id, emoji=emoji, label=label))
    return entries


def _build_embed(title: str, description: str | None, entries: list[RoleMenuEntry]) -> Embed:
    body_lines = [f"{e.emoji} <@&{e.role_id}> — {e.label}" for e in entries]
    embed_description = (description + "\n\n" if description else "") + "\n".join(body_lines)
    return Embed(title=title, description=embed_description, color=Colors.UTIL)


def _build_components(menu_id: str, entries: list[RoleMenuEntry]) -> list[ActionRow]:
    rows: list[ActionRow] = []
    for chunk_start in range(0, len(entries), 5):
        buttons = []
        for offset, entry in enumerate(entries[chunk_start : chunk_start + 5]):
            idx = chunk_start + offset
            buttons.append(
                Button(
                    label=entry.label[:80],
                    style=ButtonStyle.SECONDARY,
                    emoji=entry.emoji,
                    custom_id=f"{BUTTON_PREFIX}:{menu_id}:{idx}",
                )
            )
        rows.append(ActionRow(*buttons))
    return rows


class CommandsMixin:
    """Admin slash commands to create / list / edit / delete role menus."""

    @slash_command(
        name="rolemenu",
        description="Gérer les menus de rôles",
        sub_cmd_name="create",
        sub_cmd_description="Créer un menu de rôles dans un salon",
        scopes=enabled_servers_int,  # type: ignore
    )
    @slash_option(
        "channel",
        "Salon où publier le menu",
        opt_type=OptionType.CHANNEL,
        required=True,
        channel_types=[ChannelType.GUILD_TEXT, ChannelType.GUILD_NEWS],
    )
    @slash_option(
        "title",
        "Titre du menu",
        opt_type=OptionType.STRING,
        required=True,
    )
    @slash_option(
        "entries",
        "Entrées séparées par « ; ». Format : roleId,emoji,libellé",
        opt_type=OptionType.STRING,
        required=True,
    )
    @slash_option(
        "description",
        "Description optionnelle affichée au-dessus des rôles",
        opt_type=OptionType.STRING,
        required=False,
    )
    @slash_default_member_permission(Permissions.ADMINISTRATOR | Permissions.MANAGE_ROLES)
    async def rolemenu_create(
        self,
        ctx: SlashContext,
        channel: BaseChannel,
        title: str,
        entries: str,
        description: str | None = None,
    ) -> None:
        if not await require_guild(ctx):
            return

        parsed = _parse_entries(entries)
        if isinstance(parsed, str):
            await send_error(ctx, parsed)
            return

        # Validate role IDs against the guild before posting.
        guild_role_ids = {str(r.id) for r in ctx.guild.roles}
        for entry in parsed:
            if entry.role_id not in guild_role_ids:
                await send_error(ctx, f"Rôle introuvable : <@&{entry.role_id}> ({entry.role_id}).")
                return

        if not hasattr(channel, "send"):
            await send_error(ctx, "Ce salon ne permet pas l'envoi de messages.")
            return

        await ctx.defer(ephemeral=True)

        menu = RoleMenu(
            guild_id=str(ctx.guild_id),
            channel_id=str(channel.id),
            title=title,
            description=description,
            entries=parsed,
            created_by=str(ctx.author.id),
            created_at=datetime.now(),
        )
        menu_id = await self.repository(ctx.guild_id).add(menu)

        embed = _build_embed(title, description, parsed)
        components = _build_components(menu_id, parsed)
        try:
            sent = await channel.send(embeds=[embed], components=components)  # type: ignore[union-attr]
        except Exception as e:
            await self.repository(ctx.guild_id).delete(menu_id)
            await send_error(ctx, f"Échec de l'envoi du menu : {e}")
            return

        await self.repository(ctx.guild_id).update(menu_id, message_id=str(sent.id))
        logger.info(
            "Role menu %s created by %s in #%s with %d entries",
            menu_id,
            ctx.author.username,
            getattr(channel, "name", channel.id),
            len(parsed),
        )
        await send_success(ctx, f"Menu créé (id `{menu_id}`).")

    @rolemenu_create.subcommand(
        sub_cmd_name="list",
        sub_cmd_description="Lister les menus de rôles de ce serveur",
    )
    @slash_default_member_permission(Permissions.ADMINISTRATOR | Permissions.MANAGE_ROLES)
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
        embed = Embed(
            title="Menus de rôles",
            description="\n".join(lines),
            color=Colors.UTIL,
        )
        await ctx.send(embeds=[embed], ephemeral=True)

    @rolemenu_create.subcommand(
        sub_cmd_name="delete",
        sub_cmd_description="Supprimer un menu de rôles",
    )
    @slash_option(
        "menu_id",
        "ID du menu (visible via /rolemenu list)",
        opt_type=OptionType.STRING,
        required=True,
    )
    @slash_default_member_permission(Permissions.ADMINISTRATOR | Permissions.MANAGE_ROLES)
    async def rolemenu_delete(self, ctx: SlashContext, menu_id: str) -> None:
        if not await require_guild(ctx):
            return
        menu = await self.repository(ctx.guild_id).get(menu_id)
        if not menu:
            await send_error(ctx, "Menu introuvable.")
            return

        await self.repository(ctx.guild_id).delete(menu_id)

        if menu.message_id:
            try:
                channel = await self.bot.fetch_channel(int(menu.channel_id))
                if channel and hasattr(channel, "fetch_message"):
                    msg = await channel.fetch_message(int(menu.message_id))
                    if msg:
                        await msg.delete()
            except Exception as e:
                logger.warning("Could not delete role menu message %s: %s", menu.message_id, e)

        logger.info("Role menu %s deleted by %s", menu_id, ctx.author.username)
        await send_success(ctx, "Menu supprimé.")

    @rolemenu_create.subcommand(
        sub_cmd_name="edit",
        sub_cmd_description="Modifier le titre ou la description d'un menu existant",
    )
    @slash_option(
        "menu_id",
        "ID du menu (visible via /rolemenu list)",
        opt_type=OptionType.STRING,
        required=True,
    )
    @slash_option(
        "title",
        "Nouveau titre (optionnel)",
        opt_type=OptionType.STRING,
        required=False,
    )
    @slash_option(
        "description",
        "Nouvelle description (optionnel, laisser vide pour conserver)",
        opt_type=OptionType.STRING,
        required=False,
    )
    @slash_default_member_permission(Permissions.ADMINISTRATOR | Permissions.MANAGE_ROLES)
    async def rolemenu_edit(
        self,
        ctx: SlashContext,
        menu_id: str,
        title: str | None = None,
        description: str | None = None,
    ) -> None:
        if not await require_guild(ctx):
            return
        if title is None and description is None:
            await send_error(ctx, "Indiquez au moins un titre ou une description à modifier.")
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
                channel = await self.bot.fetch_channel(int(menu.channel_id))
                if channel and hasattr(channel, "fetch_message"):
                    msg = await channel.fetch_message(int(menu.message_id))
                    if msg:
                        embed = _build_embed(new_title, new_description, menu.entries)
                        await msg.edit(embeds=[embed])
            except Exception as e:
                logger.warning("Could not edit role menu message %s: %s", menu.message_id, e)

        await send_success(ctx, "Menu modifié.")
