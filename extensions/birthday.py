"""Birthday Extension — thin Discord glue layer.

All business logic lives in features/birthday/.
"""

import os
import random
from datetime import datetime
from typing import Optional

import pytz
from babel.dates import format_date, get_date_format
from dateutil.relativedelta import relativedelta
from interactions import (
    ActionRow,
    AutocompleteContext,
    Button,
    ButtonStyle,
    Client,
    ComponentContext,
    Embed,
    Extension,
    OptionType,
    OrTrigger,
    SlashContext,
    Task,
    TimeTrigger,
    listen,
    slash_command,
    slash_option,
)

from features.birthday import (
    BirthdayEntry,
    BirthdayRepository,
    _safe_replace_year,
    _strip_year_from_format,
)
from src.core import logging as logutil
from src.core.config import load_config
from src.core.errors import DatabaseError, ValidationError
from src.core.text import pick_weighted_message
from src.discord_ext.embeds import Colors
from src.discord_ext.messages import fetch_user_safe, require_guild
from src.discord_ext.paginator import CustomPaginator

logger = logutil.init_logger(os.path.basename(__file__))
config, module_config, enabled_servers = load_config("moduleBirthday")


def _compute_age(birth_date: datetime, reference: datetime | None = None) -> int:
    if reference is None:
        reference = datetime.now()
    return relativedelta(reference, birth_date).years


def _validate_and_parse_date(date_str: str) -> datetime:
    try:
        return datetime.strptime(date_str, "%d/%m/%Y")
    except ValueError as e:
        raise ValidationError("Date invalide. Format attendu : JJ/MM/AAAA") from e


def _validate_timezone(timezone_str: str) -> pytz.BaseTzInfo:
    if timezone_str not in pytz.all_timezones:
        raise ValidationError("Fuseau horaire invalide")
    return pytz.timezone(timezone_str)


class BirthdayExtension(Extension):
    def __init__(self, bot: Client) -> None:
        self.bot = bot

    def _repo(self, guild_id) -> BirthdayRepository:
        return BirthdayRepository(guild_id)

    @staticmethod
    def _server_config(server_id: int) -> dict:
        return module_config.get(str(server_id), {})

    async def _toggle_birthday_role(self, server, member, server_id: int, *, add: bool) -> None:
        role_id = self._server_config(server_id).get("birthdayRoleId")
        if not role_id:
            return
        try:
            role = await server.fetch_role(role_id)
            if not role:
                logger.warning(
                    "Could not fetch birthday role %s in server %s", role_id, server.name
                )
                return
            if add:
                await member.add_role(role)
                logger.info(
                    "Birthday role %s given to %s on server %s",
                    role.name,
                    member.display_name,
                    server.name,
                )
            elif role in member.roles:
                await member.remove_role(role)
                logger.info(
                    "Birthday role %s removed from %s on server %s",
                    role.name,
                    member.display_name,
                    server.name,
                )
        except Exception as e:
            action = "giving" if add else "removing"
            logger.error("Error %s birthday role: %s", action, e)

    @listen()
    async def on_startup(self) -> None:
        for guild_id in enabled_servers:
            try:
                await self._repo(guild_id).ensure_indexes()
            except Exception as e:
                logger.error("Failed to create indexes for guild %s: %s", guild_id, e)
        self.anniversaire_check.start()

    @slash_command(
        name="anniversaire",
        description="Anniversaire",
        scopes=enabled_servers,
        sub_cmd_name="ajouter",
        sub_cmd_description="Ajoute ou modifie ton anniversaire",
    )
    @slash_option(
        name="date",
        description="Date de l'anniversaire (format : JJ/MM/AAAA)",
        opt_type=OptionType.STRING,
        required=True,
        min_length=10,
        max_length=10,
    )
    @slash_option(
        name="timezone",
        description="Fuseau horaire ex : Europe/Paris",
        opt_type=OptionType.STRING,
        required=True,
        autocomplete=True,
    )
    @slash_option(
        name="hideyear",
        description="Masquer l'année",
        opt_type=OptionType.BOOLEAN,
        required=False,
    )
    async def anniversaire(
        self,
        ctx: SlashContext,
        date: str,
        timezone: str,
        hideyear: bool | None = False,
    ) -> None:
        if not await require_guild(ctx):
            return
        try:
            parsed_date = _validate_and_parse_date(date)
            validated_tz = _validate_timezone(timezone)
            repo = self._repo(ctx.guild.id)
            existing = await repo.find_one(ctx.author.id)
            entry = BirthdayEntry(
                user=ctx.author.id,
                date=parsed_date,
                timezone=validated_tz.zone,
                hideyear=hideyear or False,
                isBirthday=existing.isBirthday if existing else False,
            )
            await repo.upsert(entry)
            verb = "mis à jour" if existing else "ajouté"
            await ctx.send(f"Anniversaire {verb} ✅", ephemeral=True)
            logger.info(
                "Anniversaire de %s %s sur le serveur %s (%s)",
                ctx.author.display_name,
                verb,
                ctx.guild.name,
                parsed_date.strftime("%d/%m/%Y"),
            )
        except ValidationError as e:
            await ctx.send(str(e), ephemeral=True)
        except DatabaseError as e:
            logger.error("Database error for user %s: %s", ctx.author.display_name, e)
            await ctx.send("Erreur lors de l'enregistrement. Veuillez réessayer.", ephemeral=True)
        except Exception as e:
            logger.error("Unexpected error in anniversaire command: %s", e)
            await ctx.send("Une erreur inattendue s'est produite.", ephemeral=True)

    @anniversaire.autocomplete("timezone")
    async def anniversaire_timezone(self, ctx: AutocompleteContext) -> None:
        timezone_input = ctx.input_text.lower()
        all_tz = list(pytz.all_timezones)
        filtered = (
            [tz for tz in all_tz if timezone_input in tz.lower()][:25]
            if timezone_input
            else random.sample(all_tz, 25)
        )
        await ctx.send(choices=[{"name": tz, "value": tz} for tz in filtered])

    @anniversaire.subcommand(
        sub_cmd_name="supprimer",
        sub_cmd_description="Supprime ton anniversaire sur ce serveur",
    )
    async def anniversaire_supprimer(self, ctx: SlashContext) -> None:
        if not await require_guild(ctx):
            return
        try:
            deleted = await self._repo(ctx.guild.id).delete(ctx.author.id)
            if deleted > 0:
                await ctx.send("Anniversaire supprimé ✅", ephemeral=True)
                logger.info(
                    "Birthday removed for %s on server %s", ctx.author.display_name, ctx.guild.name
                )
            else:
                await ctx.send("Aucun anniversaire trouvé à supprimer.", ephemeral=True)
        except DatabaseError:
            await ctx.send("Erreur lors de la suppression.", ephemeral=True)

    @anniversaire.subcommand(
        sub_cmd_name="purge",
        sub_cmd_description="Supprime ton anniversaire sur tous les serveurs",
    )
    async def anniversaire_purge(self, ctx: SlashContext) -> None:
        confirm_button = Button(
            style=ButtonStyle.DANGER,
            label="Confirmer la suppression",
            custom_id="birthday_purge_confirm",
        )
        cancel_button = Button(
            style=ButtonStyle.SECONDARY, label="Annuler", custom_id="birthday_purge_cancel"
        )
        msg = await ctx.send(
            "⚠️ Es-tu sûr de vouloir supprimer ton anniversaire sur **tous** les serveurs ?",
            components=[ActionRow(confirm_button, cancel_button)],
            ephemeral=True,
        )
        try:
            button_ctx: ComponentContext = await self.bot.wait_for_component(
                components=[confirm_button, cancel_button],
                messages=msg,
                timeout=30,
            )
            if button_ctx.custom_id == "birthday_purge_confirm":
                total_deleted = sum(
                    await self._repo(gid).delete(ctx.author.id) for gid in enabled_servers
                )
                await button_ctx.edit_origin(
                    content=f"Anniversaire supprimé sur {total_deleted} serveur(s) ✅",
                    components=[],
                )
                logger.info(
                    "Birthday purged for %s on %d server(s)", ctx.author.display_name, total_deleted
                )
            else:
                await button_ctx.edit_origin(content="Suppression annulée.", components=[])
        except TimeoutError:
            await msg.edit(content="Temps écoulé, suppression annulée.", components=[])

    @anniversaire.subcommand(
        sub_cmd_name="liste",
        sub_cmd_description="Liste des anniversaires",
    )
    async def anniversaire_liste(self, ctx: SlashContext) -> None:
        if not await require_guild(ctx):
            return
        try:
            birthdays = await self._repo(ctx.guild.id).find_all()
            if not birthdays:
                await ctx.send("Aucun anniversaire enregistré sur ce serveur.", ephemeral=True)
                return

            srv_cfg = self._server_config(ctx.guild.id)
            locale = srv_cfg.get("birthdayGuildLocale", "en_US")
            raw_format = str(get_date_format("long", locale=locale))
            date_format = _strip_year_from_format(raw_format)

            birthdays.sort(key=lambda b: _safe_replace_year(b.date, 2000))

            embeds: list[Embed] = []
            lines: list[str] = []

            for entry in birthdays:
                try:
                    _, user = await fetch_user_safe(self.bot, entry.user)
                    if not user:
                        logger.warning("Could not fetch user %s", entry.user)
                        continue
                    formatted = format_date(entry.date, date_format, locale=locale)
                    if entry.hideyear:
                        lines.append(f"**{user.mention}** : {formatted}")
                    else:
                        age = _compute_age(entry.date)
                        lines.append(f"**{user.mention}** : {formatted} ({age} ans)")
                    if len(lines) % 25 == 0:
                        embeds.append(
                            Embed(
                                title="Anniversaires 🎂",
                                description="\n".join(lines),
                                color=Colors.SUCCESS,
                            )
                        )
                        lines = []
                except Exception as e:
                    logger.error("Error processing birthday for user %s: %s", entry.user, e)

            if lines:
                embeds.append(
                    Embed(
                        title="Anniversaires 🎂", description="\n".join(lines), color=Colors.SUCCESS
                    )
                )

            if not embeds:
                await ctx.send("Impossible de récupérer les anniversaires.", ephemeral=True)
                return

            paginator = CustomPaginator.create_from_embeds(self.bot, *embeds, timeout=3600)
            await paginator.send(ctx)

        except Exception as e:
            logger.error("Error in anniversaire_liste: %s", e)
            await ctx.send("Erreur lors de la récupération des anniversaires.", ephemeral=True)

    @Task.create(OrTrigger(*[TimeTrigger(hour, 0) for hour in range(24)]))
    async def anniversaire_check(self) -> None:
        logger.debug("Starting birthday check task")
        try:
            for guild_id in enabled_servers:
                try:
                    birthdays = await self._repo(guild_id).find_all()
                    logger.debug(
                        "Found %d birthdays to check for guild %s", len(birthdays), guild_id
                    )
                    for entry in birthdays:
                        try:
                            await self._process_birthday(entry, int(guild_id))
                        except Exception as e:
                            logger.error("Error processing birthday for user %s: %s", entry.user, e)
                except Exception as e:
                    logger.error("Error loading birthdays for guild %s: %s", guild_id, e)
        except Exception as e:
            logger.error("Critical error in anniversaire_check task: %s", e)

    async def _process_birthday(self, entry: BirthdayEntry, guild_id: int) -> None:
        timezone = pytz.timezone(entry.timezone)
        now_tz = datetime.now(timezone)
        birthday_today = _safe_replace_year(entry.date, now_tz.year)
        logger.debug(
            "Processing birthday – now: %s, birthday this year: %s",
            now_tz.date(),
            birthday_today.date(),
        )

        if now_tz.date() == birthday_today.date():
            await self._handle_birthday_celebration(entry, guild_id, now_tz)
        else:
            await self._handle_birthday_end(entry, guild_id)

    async def _handle_birthday_celebration(
        self, entry: BirthdayEntry, guild_id: int, now_tz: datetime
    ) -> None:
        if entry.isBirthday:
            return
        repo = self._repo(guild_id)
        await repo.update_fields(entry.user, {"isBirthday": True})
        try:
            server = await self.bot.fetch_guild(guild_id)
            if not server:
                logger.warning("Could not fetch server %s", guild_id)
                return
            member = await server.fetch_member(entry.user)
            if not member:
                logger.warning("Could not fetch member %s in server %s", entry.user, server.name)
                return
            srv_cfg = self._server_config(guild_id)
            channel_id = srv_cfg.get("birthdayChannelId")
            channel = (
                await server.fetch_channel(channel_id) if channel_id else server.system_channel
            )
            if not channel:
                logger.warning("No valid channel for birthday message in server %s", server.name)
                return
            age = _compute_age(entry.date, now_tz.replace(tzinfo=None))
            await self._send_birthday_message(channel, member, age, guild_id)
            await self._toggle_birthday_role(server, member, guild_id, add=True)
            logger.info(
                "Birthday celebration completed for %s on server %s (%d years old)",
                member.display_name,
                server.name,
                age,
            )
        except Exception as e:
            logger.error("Error handling birthday celebration: %s", e)

    async def _handle_birthday_end(self, entry: BirthdayEntry, guild_id: int) -> None:
        if not entry.isBirthday:
            return
        await self._repo(guild_id).update_fields(entry.user, {"isBirthday": False})
        try:
            server = await self.bot.fetch_guild(guild_id)
            if not server:
                return
            member = await server.fetch_member(entry.user)
            if not member:
                return
            logger.info("Birthday ended for %s on server %s", member.display_name, server.name)
            await self._toggle_birthday_role(server, member, guild_id, add=False)
        except Exception as e:
            logger.error("Error handling birthday end: %s", e)

    async def _send_birthday_message(self, channel, member, age: int, server_id: int) -> None:
        try:
            srv_cfg = self._server_config(server_id)
            text = pick_weighted_message(
                srv_cfg,
                "birthdayMessageList",
                "birthdayMessageWeights",
                "Joyeux anniversaire {mention} ! 🎉",
                mention=member.mention,
                age=age,
            )
            await channel.send(text)
            logger.debug("Birthday message sent for %s", member.display_name)
        except Exception as e:
            logger.error("Error sending birthday message: %s", e)
