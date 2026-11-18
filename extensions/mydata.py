"""``/mes-donnees``: a member exports or deletes what the bot keeps about them (F10).

Both act on every server at once (``Privacy.md`` §9 used to send members to
the Operator by e-mail). The export is a JSON file attached to an ephemeral
reply, seen by the member alone. The deletion asks for confirmation, then
deletes what is the member's alone, anonymises what they share and keeps what
must stay (see :mod:`features.userdata`), and says which.

The same deletion runs for one server 30 days after a member leaves it,
cancelled if they come back in the meantime (a daily task).
"""

import io
import os

from interactions import (
    ActionRow,
    Button,
    ButtonStyle,
    Client,
    ContextType,
    Extension,
    File,
    SlashContext,
    Task,
    TimeTrigger,
    listen,
    slash_command,
)
from interactions.api.events import MemberAdd, MemberRemove

from features.userdata import DeparturesRepository, Report, UserDataRepository, export_json
from src.core import clock
from src.core import logging as logutil
from src.core.db import mongo_manager
from src.core.errors import DatabaseError
from src.discord_ext.messages import edit_message_if_changed

logger = logutil.init_logger(os.path.basename(__file__))

CONFIRM_ID = "mydata_delete_confirm"
CANCEL_ID = "mydata_delete_cancel"
# Discord's attachment limit for a bot without boosts.
MAX_EXPORT_BYTES = 8 * 1024 * 1024

# A collection name, not a secret (detect-secrets reads the keyword in it).
SANTA_DRAWS = "secret_santa_draw_results"  # pragma: allowlist secret
# Why something is kept, in the member's words.
KEPT_REASONS = {
    "infractions": "les sanctions de modération (elles protègent les serveurs)",
    SANTA_DRAWS: "les tirages Secret Santa déjà faits (les cadeaux des autres en dépendent)",
    "tricount": "tes comptes Tricount, tant qu'il te reste un solde à régler",
}
NOT_COVERED = (
    "Hors de portée de cette commande : ce que les admins d'un serveur ont écrit dans sa "
    "configuration (un prénom, un compte Spotify lié) et un compte LLDAP créé avec /inscription. "
    "Demande-les aux admins du serveur."
)


def summary(report: Report) -> str:
    lines = [
        f"🗑️ Supprimé ou anonymisé : {report.total} élément(s) "
        f"({sum(report.deleted.values())} supprimé(s), {sum(report.anonymised.values())} anonymisé(s))."
    ]
    kept = sorted({where.rsplit(".", 1)[1] for where in report.kept})
    if kept:
        lines.append("Conservé : " + " ; ".join(KEPT_REASONS.get(k, k) for k in kept) + ".")
    lines.append(NOT_COVERED)
    return "\n".join(lines)


class MyDataExtension(Extension):
    """Export or delete a member's data; forget members 30 days after they leave."""

    MODULE_KEYS = ()
    HELP_LABEL = "🔐 Mes données"

    def __init__(self, bot: Client):
        self.bot: Client = bot

    @listen()
    async def on_startup(self) -> None:
        if not self.forget_departed_members.running:
            self.forget_departed_members.start()

    @slash_command(
        name="mes-donnees",
        description="Tes données gardées par le bot, sur tous les serveurs",
        sub_cmd_name="exporter",
        sub_cmd_description="Recevoir (en privé) tout ce que le bot garde sur toi",
        contexts=[ContextType.GUILD, ContextType.BOT_DM],
    )
    async def export(self, ctx: SlashContext) -> None:
        await ctx.defer(ephemeral=True)
        data = await UserDataRepository(ctx.author.id).export(
            await mongo_manager.guild_ids_with_data()
        )
        payload = export_json(data)
        if len(payload) > MAX_EXPORT_BYTES:
            await ctx.send(
                "L'export est trop gros pour Discord : demande-le à l'opérateur du bot.",
                ephemeral=True,
            )
            return
        await ctx.send(
            "Voici tout ce que le bot garde sur toi (fichier visible de toi seul).",
            file=File(io.BytesIO(payload), file_name="mes-donnees.json"),
            ephemeral=True,
        )
        logger.info("Data export sent to %s (%d bytes)", ctx.author.id, len(payload))

    @export.subcommand(
        sub_cmd_name="supprimer",
        sub_cmd_description="Effacer ce que le bot garde sur toi, sur tous les serveurs",
    )
    async def delete(self, ctx: SlashContext) -> None:
        confirm = Button(style=ButtonStyle.DANGER, label="Tout effacer", custom_id=CONFIRM_ID)
        cancel = Button(style=ButtonStyle.SECONDARY, label="Annuler", custom_id=CANCEL_ID)
        message = await ctx.send(
            "⚠️ Effacer ce que le bot garde sur toi, **sur tous les serveurs** ? Ton XP, ton "
            "anniversaire et tes rappels sont supprimés ; tes votes et ce que tu as proposé "
            "restent, sans ton nom. C'est définitif.",
            components=[ActionRow(confirm, cancel)],
            ephemeral=True,
        )
        try:
            pressed = await self.bot.wait_for_component(
                components=[confirm, cancel], messages=message, timeout=60
            )
        except TimeoutError:
            await edit_message_if_changed(
                message, content="Temps écoulé : rien n'a été effacé.", components=[], logger=logger
            )
            return
        button = pressed.ctx
        if button.custom_id != CONFIRM_ID:
            await button.edit_origin(content="Annulé : rien n'a été effacé.", components=[])
            return
        await button.defer(edit_origin=True)
        report = await UserDataRepository(ctx.author.id).forget(
            await mongo_manager.guild_ids_with_data()
        )
        await button.edit_origin(content=summary(report), components=[])
        logger.info(
            "Data of %s deleted on request: %d deleted, %d anonymised, kept %s",
            ctx.author.id,
            sum(report.deleted.values()),
            sum(report.anonymised.values()),
            report.kept,
        )

    @listen(MemberRemove)
    async def on_member_remove(self, event: MemberRemove) -> None:
        if getattr(event.member, "bot", False):
            return
        try:
            await DeparturesRepository().schedule(
                str(event.guild_id), str(event.member.id), clock.now()
            )
        except DatabaseError as e:
            logger.warning("Departure of %s not recorded: %s", event.member.id, e)

    @listen(MemberAdd)
    async def on_member_add(self, event: MemberAdd) -> None:
        try:
            await DeparturesRepository().cancel(str(event.guild_id), str(event.member.id))
        except DatabaseError as e:
            logger.warning("Return of %s not recorded: %s", event.member.id, e)

    @Task.create(TimeTrigger(hour=4, minute=53, utc=False))
    async def forget_departed_members(self) -> None:
        """Daily: forget, on that server, the members who left it 30 days ago."""
        departures = DeparturesRepository()
        for guild_id, user_id in await departures.due(clock.now()):
            guild = self.bot.get_guild(int(guild_id))
            if guild is not None and guild.get_member(int(user_id)) is not None:
                await departures.cancel(guild_id, user_id)  # back without an event we saw
                continue
            report = await UserDataRepository(user_id).forget([guild_id], include_global=False)
            await departures.done(guild_id, user_id)
            logger.info(
                "Member %s forgotten on guild %s, 30 days after leaving (%d items)",
                user_id,
                guild_id,
                report.total,
            )


def setup(bot: Client) -> None:
    MyDataExtension(bot)
