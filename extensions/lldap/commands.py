"""`/register` (members) and `/lldap sync` (admins)."""

from interactions import (
    Embed,
    OptionType,
    Permissions,
    SlashContext,
    slash_command,
    slash_default_member_permission,
    slash_option,
)

from features.lldap import (
    USERNAME_MAX_LENGTH,
    describe_report,
    generate_temp_password,
    normalize_email,
    normalize_username,
    username_from_discord,
)
from src.core import logging as logutil
from src.discord_ext.autocomplete import is_guild_enabled
from src.discord_ext.embeds import Colors
from src.discord_ext.messages import require_guild, send_error
from src.discord_ext.scopes import guild_scopes
from src.integrations.lldap import LldapError

from ._common import (
    ALREADY_LINKED,
    BAD_DISCORD_USERNAME,
    BAD_EMAIL,
    BAD_USERNAME,
    EMAIL_TAKEN,
    NO_MAPPING,
    NOT_CONFIGURED,
    NOT_ENABLED,
    USERNAME_TAKEN,
    enabled_servers,
    guild_role_groups,
    guild_settings,
    login_url,
    service_name,
)

logger = logutil.init_logger(__name__)


class CommandsMixin:
    """Account creation for members and the manual sync for admins.

    Both commands exist only on the guilds where the module is enabled (the
    dashboard syncs a guild's commands when the module is toggled there). The
    runtime enabled check stays as a backstop for a sync that failed.
    """

    async def _check_guild(self, ctx: SlashContext) -> str | None:
        """The guild id, or ``None`` after telling the user why the command can't run."""
        if not await require_guild(ctx):
            return None
        if not is_guild_enabled(ctx.guild.id, enabled_servers):
            await send_error(ctx, NOT_ENABLED)
            return None
        return str(ctx.guild.id)

    @slash_command(
        name="register",
        description="Crée ton compte LLDAP grâce à tes rôles Discord",
        scopes=guild_scopes(enabled_servers),
    )
    @slash_option(
        name="email",
        description="Ton adresse e-mail",
        opt_type=OptionType.STRING,
        required=True,
        max_length=254,
    )
    @slash_option(
        name="username",
        description="Nom d'utilisateur souhaité (par défaut : ton pseudo Discord)",
        opt_type=OptionType.STRING,
        required=False,
        max_length=USERNAME_MAX_LENGTH,
    )
    async def register_command(
        self, ctx: SlashContext, email: str, username: str | None = None
    ) -> None:
        guild_id = await self._check_guild(ctx)
        if guild_id is None:
            return
        settings = guild_settings(guild_id)
        if not settings.configured:
            await send_error(ctx, NOT_CONFIGURED)
            return
        mapping, _invalid = guild_role_groups(guild_id)
        if not mapping:
            await send_error(ctx, NO_MAPPING)
            return
        member = ctx.author
        groups = sorted({group for role, group in mapping.items() if member.has_role(role)})
        if not groups:
            roles = ", ".join(f"<@&{role}>" for role in mapping)
            await send_error(ctx, f"Il faut l'un de ces rôles pour créer un compte : {roles}.")
            return
        address = normalize_email(email)
        if address is None:
            await send_error(ctx, BAD_EMAIL)
            return
        if username:
            user_id = normalize_username(username)
            if user_id is None:
                await send_error(ctx, BAD_USERNAME)
                return
        else:
            user_id = username_from_discord(member.username)
            if user_id is None:
                await send_error(ctx, BAD_DISCORD_USERNAME)
                return

        await ctx.defer(ephemeral=True)
        discord_id = str(member.id)
        password = generate_temp_password()
        async with self._register_lock:
            try:
                if await self.lldap.find_user_ids(settings, settings.discord_attribute, discord_id):
                    await send_error(ctx, ALREADY_LINKED)
                    return
                if await self.lldap.find_user_ids(settings, "email", address):
                    await send_error(ctx, EMAIL_TAKEN)
                    return
                await self.lldap.create_user(
                    settings,
                    user_id=user_id,
                    email=address,
                    display_name=member.display_name,
                    discord_id=discord_id,
                )
            except LldapError as e:
                if e.conflict:
                    await send_error(ctx, USERNAME_TAKEN)
                else:
                    logger.error("Guild %s: LLDAP account creation failed: %s", guild_id, e)
                    await send_error(ctx, f"Impossible de créer le compte : {e}")
                return
            try:
                await self.lldap.set_password(settings, user_id, password)
                for group_id in groups:
                    await self.lldap.add_to_group(settings, user_id, group_id)
            except LldapError as e:
                logger.error(
                    "Guild %s: LLDAP setup of %s failed, rolling back: %s", guild_id, user_id, e
                )
                try:
                    await self.lldap.delete_user(settings, user_id)
                except LldapError as rollback_error:
                    logger.error(
                        "Guild %s: could not delete half-created LLDAP user %s: %s",
                        guild_id,
                        user_id,
                        rollback_error,
                    )
                await send_error(ctx, f"Impossible de finaliser le compte : {e}")
                return

        logger.info(
            "Guild %s: created LLDAP user %s for %s (%s), groups %s",
            guild_id,
            user_id,
            member.username,
            discord_id,
            groups,
        )
        service = service_name(guild_id)
        embed = Embed(
            title=f"Compte {service} créé",
            description=(
                "Voici tes identifiants. Change ce mot de passe temporaire dès ta première "
                "connexion."
            ),
            color=Colors.SUCCESS,
        )
        embed.add_field(name="Nom d'utilisateur", value=f"`{user_id}`", inline=True)
        embed.add_field(name="Mot de passe temporaire", value=f"`{password}`", inline=True)
        # Not shortened: members should see which site they are typing their password into.
        embed.add_field(name="Connexion", value=login_url(guild_id, settings), inline=False)
        await ctx.send(embeds=[embed], ephemeral=True)

    @slash_command(
        name="lldap",
        description="Les commandes d'administration LLDAP",
        scopes=guild_scopes(enabled_servers),
    )
    @slash_default_member_permission(Permissions.ADMINISTRATOR)
    async def lldap_command(self, ctx: SlashContext) -> None:
        pass

    @lldap_command.subcommand(
        sub_cmd_name="sync",
        sub_cmd_description="Synchronise maintenant les groupes LLDAP avec les rôles Discord",
    )
    async def lldap_sync(self, ctx: SlashContext) -> None:
        guild_id = await self._check_guild(ctx)
        if guild_id is None:
            return
        if not guild_settings(guild_id).configured:
            await send_error(ctx, NOT_CONFIGURED)
            return
        await ctx.defer(ephemeral=True)
        report = await self.run_sync([guild_id])
        logger.info(
            "Guild %s: manual LLDAP sync by %s (%s)", guild_id, ctx.author.username, ctx.author.id
        )
        await ctx.send(describe_report(report), ephemeral=True)
