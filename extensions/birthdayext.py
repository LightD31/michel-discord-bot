"""
Birthday Extension for Discord Bot

This extension manages user birthdays with features including:
- Adding/updating birthdays with timezone support
- Automatic birthday notifications
- Birthday role management
- Listing all birthdays with pagination
- Robust error handling and validation

Author: Improved by Assistant
Version: 3.0
"""

import asyncio
import os
import re
import random
from datetime import datetime
from typing import Optional

import pymongo
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

from src import logutil
from src.mongodb import mongo_manager
from src.utils import CustomPaginator, load_config

logger = logutil.init_logger(os.path.basename(__file__))
config, module_config, enabled_servers = load_config("moduleBirthday")


# ---------------------------------------------------------------------------
# Custom exception classes
# ---------------------------------------------------------------------------

class BirthdayError(Exception):
    """Exception de base pour l'extension Birthday."""
    pass


class DatabaseError(BirthdayError):
    """Exception levée en cas d'échec d'opération base de données."""
    pass


class ValidationError(BirthdayError):
    """Exception levée en cas de validation de données incorrecte."""
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_replace_year(dt: datetime, year: int) -> datetime:
    """Remplace l'année de *dt* en gérant le 29 février.

    Si *dt* est le 29 février et que *year* n'est pas bissextile,
    retourne le 1er mars.
    """
    try:
        return dt.replace(year=year)
    except ValueError:
        return dt.replace(year=year, month=3, day=1)


def _strip_year_from_format(date_format: str) -> str:
    """Supprime les tokens d'année (y, yy, yyyy …) d'un format de date babel/ICU.

    Utilise une regex pour ne cibler que les tokens d'année isolés.
    """
    cleaned = re.sub(r"[,/\-.\s]*y+[,/\-.\s]*", " ", date_format)
    return cleaned.strip(" ,.-/")


def _compute_age(birth_date: datetime, reference: Optional[datetime] = None) -> int:
    """Calcule l'âge en années complètes entre *birth_date* et *reference*."""
    if reference is None:
        reference = datetime.now()
    return relativedelta(reference, birth_date).years


# ---------------------------------------------------------------------------
# Extension
# ---------------------------------------------------------------------------

class BirthdayClass(Extension):
    def __init__(self, bot: Client) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # Per-guild collection helper
    # ------------------------------------------------------------------

    @staticmethod
    def _get_col(guild_id):
        """Retourne la collection birthday du guild."""
        return mongo_manager.get_guild_collection(str(guild_id), "birthday")

    # ------------------------------------------------------------------
    # Ensure indexes (called once on startup)
    # ------------------------------------------------------------------

    async def _ensure_indexes(self) -> None:
        """Crée les index nécessaires pour chaque serveur activé."""
        for guild_id in enabled_servers:
            try:
                col = self._get_col(guild_id)
                await col.create_index(
                    [("user", pymongo.ASCENDING)],
                    unique=True,
                )
            except Exception as e:
                logger.error("Failed to create indexes for guild %s: %s", guild_id, e)

    # ------------------------------------------------------------------
    # Database helpers (motor async natif)
    # ------------------------------------------------------------------

    async def _db_find_one(self, guild_id, query: dict) -> Optional[dict]:
        """Trouve un document."""
        try:
            return await self._get_col(guild_id).find_one(query)
        except Exception as e:
            logger.error("DB find_one failed: %s", e)
            raise DatabaseError(f"Failed to query database: {e}")

    async def _db_find(self, guild_id, query: dict) -> list[dict]:
        """Trouve plusieurs documents."""
        try:
            return await self._get_col(guild_id).find(query).to_list(length=None)
        except Exception as e:
            logger.error("DB find failed: %s", e)
            raise DatabaseError(f"Failed to query database: {e}")

    async def _db_update_one(self, guild_id, query: dict, update: dict) -> None:
        """Met à jour un document."""
        try:
            await self._get_col(guild_id).update_one(query, update)
        except Exception as e:
            logger.error("DB update_one failed: %s", e)
            raise DatabaseError(f"Failed to update database: {e}")

    async def _db_insert_one(self, guild_id, document: dict) -> None:
        """Insère un document."""
        try:
            await self._get_col(guild_id).insert_one(document)
        except Exception as e:
            logger.error("DB insert_one failed: %s", e)
            raise DatabaseError(f"Failed to insert into database: {e}")

    async def _db_delete_one(self, guild_id, query: dict) -> int:
        """Supprime un document. Retourne le nombre supprimé."""
        try:
            result = await self._get_col(guild_id).delete_one(query)
            return result.deleted_count
        except Exception as e:
            logger.error("DB delete_one failed: %s", e)
            raise DatabaseError(f"Failed to delete from database: {e}")

    async def _db_delete_many(self, guild_id, query: dict) -> int:
        """Supprime plusieurs documents. Retourne le nombre supprimé."""
        try:
            result = await self._get_col(guild_id).delete_many(query)
            return result.deleted_count
        except Exception as e:
            logger.error("DB delete_many failed: %s", e)
            raise DatabaseError(f"Failed to delete from database: {e}")

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_and_parse_date(date_str: str) -> datetime:
        """Valide et parse une chaîne de date (JJ/MM/AAAA) en datetime."""
        try:
            return datetime.strptime(date_str, "%d/%m/%Y")
        except ValueError:
            raise ValidationError("Date invalide. Format attendu : JJ/MM/AAAA")

    @staticmethod
    def _validate_timezone(timezone_str: str) -> pytz.BaseTzInfo:
        """Valide un fuseau horaire et retourne l'objet tz correspondant."""
        if timezone_str not in pytz.all_timezones:
            raise ValidationError("Fuseau horaire invalide")
        return pytz.timezone(timezone_str)

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _server_config(server_id: int) -> dict:
        """Retourne la config du module pour un serveur donné."""
        return module_config.get(str(server_id), {})

    # ------------------------------------------------------------------
    # Role helper (factorisé give / remove)
    # ------------------------------------------------------------------

    async def _toggle_birthday_role(self, server, member, server_id: int, *, add: bool) -> None:
        """Ajoute ou retire le rôle d'anniversaire pour *member* sur *server*."""
        role_id = self._server_config(server_id).get("birthdayRoleId")
        if not role_id:
            return

        try:
            role = await server.fetch_role(role_id)
            if not role:
                logger.warning("Could not fetch birthday role %s in server %s", role_id, server.name)
                return

            if add:
                await member.add_role(role)
                logger.info("Birthday role %s given to %s on server %s", role.name, member.display_name, server.name)
            elif role in member.roles:
                await member.remove_role(role)
                logger.info("Birthday role %s removed from %s on server %s", role.name, member.display_name, server.name)
        except Exception as e:
            action = "giving" if add else "removing"
            logger.error("Error %s birthday role: %s", action, e)

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    @listen()
    async def on_startup(self) -> None:
        await self._ensure_indexes()
        self.anniversaire_check.start()

    # ------------------------------------------------------------------
    # /anniversaire ajouter
    # ------------------------------------------------------------------

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
        hideyear: Optional[bool] = False,
    ) -> None:
        """Ajoute ou modifie l'anniversaire d'un utilisateur."""
        if not ctx.guild:
            await ctx.send("Cette commande ne peut être utilisée que dans un serveur.", ephemeral=True)
            return

        try:
            parsed_date = self._validate_and_parse_date(date)
            validated_tz = self._validate_timezone(timezone)

            guild_id = ctx.guild.id
            query = {"user": ctx.author.id}
            existing = await self._db_find_one(guild_id, query)

            if existing:
                await self._db_update_one(guild_id, query, {
                    "$set": {
                        "date": parsed_date,
                        "timezone": validated_tz.zone,
                        "hideyear": hideyear or False,
                    }
                })
                await ctx.send("Anniversaire mis à jour ✅", ephemeral=True)
                logger.info(
                    "Anniversaire de %s mis à jour sur le serveur %s (%s)",
                    ctx.author.display_name, ctx.guild.name, parsed_date.strftime("%d/%m/%Y"),
                )
            else:
                await self._db_insert_one(guild_id, {
                    "user": ctx.author.id,
                    "date": parsed_date,
                    "timezone": validated_tz.zone,
                    "hideyear": hideyear or False,
                    "isBirthday": False,
                })
                await ctx.send("Anniversaire ajouté ✅", ephemeral=True)
                logger.info(
                    "Anniversaire de %s ajouté sur le serveur %s (%s)",
                    ctx.author.display_name, ctx.guild.name, parsed_date.strftime("%d/%m/%Y"),
                )

        except ValidationError as e:
            await ctx.send(str(e), ephemeral=True)
        except DatabaseError as e:
            logger.error("Database error for user %s: %s", ctx.author.display_name, e)
            await ctx.send("Erreur lors de l'enregistrement. Veuillez réessayer.", ephemeral=True)
        except Exception as e:
            logger.error("Unexpected error in anniversaire command: %s", e)
            await ctx.send("Une erreur inattendue s'est produite.", ephemeral=True)

    # ------------------------------------------------------------------
    # Timezone autocomplete
    # ------------------------------------------------------------------

    @anniversaire.autocomplete("timezone")
    async def anniversaire_timezone(self, ctx: AutocompleteContext) -> None:
        timezone_input = ctx.input_text.lower()
        all_tz = list(pytz.all_timezones)

        if timezone_input:
            filtered = [tz for tz in all_tz if timezone_input in tz.lower()][:25]
        else:
            filtered = random.sample(all_tz, 25)

        await ctx.send(choices=[{"name": tz, "value": tz} for tz in filtered])

    # ------------------------------------------------------------------
    # /anniversaire supprimer
    # ------------------------------------------------------------------

    @anniversaire.subcommand(
        sub_cmd_name="supprimer",
        sub_cmd_description="Supprime ton anniversaire sur ce serveur",
    )
    async def anniversaire_supprimer(self, ctx: SlashContext) -> None:
        """Supprime l'anniversaire de l'utilisateur sur le serveur courant."""
        if not ctx.guild:
            await ctx.send("Cette commande ne peut être utilisée que dans un serveur.", ephemeral=True)
            return

        try:
            deleted = await self._db_delete_one(ctx.guild.id, {"user": ctx.author.id})
            if deleted > 0:
                await ctx.send("Anniversaire supprimé ✅", ephemeral=True)
                logger.info("Birthday removed for %s on server %s", ctx.author.display_name, ctx.guild.name)
            else:
                await ctx.send("Aucun anniversaire trouvé à supprimer.", ephemeral=True)
        except DatabaseError:
            await ctx.send("Erreur lors de la suppression.", ephemeral=True)

    # ------------------------------------------------------------------
    # /anniversaire purge (avec confirmation)
    # ------------------------------------------------------------------

    @anniversaire.subcommand(
        sub_cmd_name="purge",
        sub_cmd_description="Supprime ton anniversaire sur tous les serveurs",
    )
    async def anniversaire_purge(self, ctx: SlashContext) -> None:
        """Supprime l'anniversaire de l'utilisateur sur tous les serveurs (avec confirmation)."""
        confirm_button = Button(
            style=ButtonStyle.DANGER,
            label="Confirmer la suppression",
            custom_id="birthday_purge_confirm",
        )
        cancel_button = Button(
            style=ButtonStyle.SECONDARY,
            label="Annuler",
            custom_id="birthday_purge_cancel",
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
                total_deleted = 0
                for gid in enabled_servers:
                    total_deleted += await self._db_delete_one(gid, {"user": ctx.author.id})
                await button_ctx.edit_origin(
                    content=f"Anniversaire supprimé sur {total_deleted} serveur(s) ✅",
                    components=[],
                )
                logger.info("Birthday purged for %s on %d server(s)", ctx.author.display_name, total_deleted)
            else:
                await button_ctx.edit_origin(content="Suppression annulée.", components=[])

        except TimeoutError:
            await msg.edit(content="Temps écoulé, suppression annulée.", components=[])

    # ------------------------------------------------------------------
    # /anniversaire liste
    # ------------------------------------------------------------------

    @anniversaire.subcommand(
        sub_cmd_name="liste",
        sub_cmd_description="Liste des anniversaires",
    )
    async def anniversaire_liste(self, ctx: SlashContext) -> None:
        """Affiche la liste paginée de tous les anniversaires du serveur."""
        if not ctx.guild:
            await ctx.send("Cette commande ne peut être utilisée que dans un serveur.", ephemeral=True)
            return

        try:
            birthdays = await self._db_find(ctx.guild.id, {})

            if not birthdays:
                await ctx.send("Aucun anniversaire enregistré sur ce serveur.", ephemeral=True)
                return

            # Locale / format de date
            srv_cfg = self._server_config(ctx.guild.id)
            locale = srv_cfg.get("birthdayGuildLocale", "en_US")
            raw_format = str(get_date_format("long", locale=locale))
            date_format = _strip_year_from_format(raw_format)

            # Tri par mois/jour (gestion du 29 février)
            birthdays.sort(key=lambda b: _safe_replace_year(b["date"], 2000))

            # Construction des embeds paginés (25 entrées par page)
            embeds: list[Embed] = []
            lines: list[str] = []

            for birthday in birthdays:
                try:
                    bd_date: datetime = birthday["date"]
                    uid = birthday["user"]

                    # Préfère le cache Discord, fallback sur l'API
                    user = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
                    if not user:
                        logger.warning("Could not fetch user %s", uid)
                        continue

                    hideyear: bool = birthday.get("hideyear", False)
                    formatted = format_date(bd_date, date_format, locale=locale)

                    if hideyear:
                        lines.append(f"**{user.mention}** : {formatted}")
                    else:
                        age = _compute_age(bd_date)
                        lines.append(f"**{user.mention}** : {formatted} ({age} ans)")

                    if len(lines) % 25 == 0:
                        embeds.append(Embed(title="Anniversaires 🎂", description="\n".join(lines), color=0x00FF00))
                        lines = []

                except Exception as e:
                    logger.error("Error processing birthday for user %s: %s", birthday.get("user"), e)
                    continue

            if lines:
                embeds.append(Embed(title="Anniversaires 🎂", description="\n".join(lines), color=0x00FF00))

            if not embeds:
                await ctx.send("Impossible de récupérer les anniversaires.", ephemeral=True)
                return

            paginator = CustomPaginator.create_from_embeds(self.bot, *embeds, timeout=3600)
            await paginator.send(ctx)

        except Exception as e:
            logger.error("Error in anniversaire_liste: %s", e)
            await ctx.send("Erreur lors de la récupération des anniversaires.", ephemeral=True)

    # ------------------------------------------------------------------
    # Vérification planifiée des anniversaires (toutes les heures)
    # ------------------------------------------------------------------

    @Task.create(OrTrigger(*[TimeTrigger(hour, 0) for hour in range(24)]))
    async def anniversaire_check(self) -> None:
        """Vérifie les anniversaires toutes les heures et gère les rôles / messages."""
        logger.debug("Starting birthday check task")

        try:
            # Itérer chaque serveur activé et charger les anniversaires depuis sa DB
            for guild_id in enabled_servers:
                try:
                    birthdays = await self._db_find(guild_id, {})
                    logger.debug("Found %d birthdays to check for guild %s", len(birthdays), guild_id)
                    for birthday in birthdays:
                        try:
                            birthday["_guild_id"] = int(guild_id)
                            await self._process_birthday(birthday)
                        except Exception as e:
                            logger.error("Error processing birthday for user %s: %s", birthday.get("user", "unknown"), e)
                except Exception as e:
                    logger.error("Error loading birthdays for guild %s: %s", guild_id, e)

        except Exception as e:
            logger.error("Critical error in anniversaire_check task: %s", e)

    # ------------------------------------------------------------------
    # Traitement interne des anniversaires
    # ------------------------------------------------------------------

    async def _process_birthday(self, birthday: dict) -> None:
        """Traite une entrée d'anniversaire individuelle."""
        date: datetime = birthday["date"]
        timezone = pytz.timezone(birthday["timezone"])
        now_tz = datetime.now(timezone)

        # Gestion du 29 février
        birthday_today = _safe_replace_year(date, now_tz.year)

        logger.debug("Processing birthday – now: %s, birthday this year: %s", now_tz.date(), birthday_today.date())

        if now_tz.date() == birthday_today.date():
            await self._handle_birthday_celebration(birthday, now_tz, date)
        else:
            await self._handle_birthday_end(birthday)

    async def _handle_birthday_celebration(self, birthday: dict, now_tz: datetime, birth_date: datetime) -> None:
        """Envoie le message d'anniversaire et attribue le rôle."""
        if birthday.get("isBirthday", False):
            return

        guild_id = birthday["_guild_id"]
        query = {"user": birthday["user"]}
        await self._db_update_one(guild_id, query, {"$set": {"isBirthday": True}})

        try:
            server = await self.bot.fetch_guild(guild_id)
            if not server:
                logger.warning("Could not fetch server %s", guild_id)
                return

            member = await server.fetch_member(birthday["user"])
            if not member:
                logger.warning("Could not fetch member %s in server %s", birthday["user"], server.name)
                return

            # Détermination du salon
            srv_cfg = self._server_config(guild_id)
            channel_id = srv_cfg.get("birthdayChannelId")
            channel = await server.fetch_channel(channel_id) if channel_id else server.system_channel

            if not channel:
                logger.warning("No valid channel for birthday message in server %s", server.name)
                return

            age = _compute_age(birth_date, now_tz.replace(tzinfo=None))
            await self._send_birthday_message(channel, member, age, guild_id)
            await self._toggle_birthday_role(server, member, guild_id, add=True)

            logger.info(
                "Birthday celebration completed for %s on server %s (%d years old)",
                member.display_name, server.name, age,
            )

        except Exception as e:
            logger.error("Error handling birthday celebration: %s", e)

    async def _handle_birthday_end(self, birthday: dict) -> None:
        """Retire le rôle d'anniversaire quand la journée est passée."""
        if not birthday.get("isBirthday", False):
            return

        guild_id = birthday["_guild_id"]
        query = {"user": birthday["user"]}
        await self._db_update_one(guild_id, query, {"$set": {"isBirthday": False}})

        try:
            server = await self.bot.fetch_guild(guild_id)
            if not server:
                return

            member = await server.fetch_member(birthday["user"])
            if not member:
                return

            logger.info("Birthday ended for %s on server %s", member.display_name, server.name)
            await self._toggle_birthday_role(server, member, guild_id, add=False)

        except Exception as e:
            logger.error("Error handling birthday end: %s", e)

    async def _send_birthday_message(self, channel, member, age: int, server_id: int) -> None:
        """Envoie un message d'anniversaire aléatoire dans le salon configuré."""
        try:
            srv_cfg = self._server_config(server_id)
            messages = srv_cfg.get("birthdayMessageList", ["Joyeux anniversaire {mention} ! 🎉"])
            weights = srv_cfg.get("birthdayMessageWeights", [1] * len(messages))

            template = random.choices(messages, weights)[0]
            await channel.send(template.format(mention=member.mention, age=age))
            logger.debug("Birthday message sent for %s", member.display_name)

        except Exception as e:
            logger.error("Error sending birthday message: %s", e)
