"""CorporationMixin — daily corporation recap and /corpo command."""

from datetime import date as date_type
from datetime import datetime

from interactions import (
    OptionType,
    SlashContext,
    Task,
    TimeTrigger,
    slash_command,
    slash_option,
)

from features.coloc.api_client import ZuniversAPIError
from features.coloc.constants import (
    ACTION_TYPE_NAMES,
    CURRENCY_EMOJI,
    DEFAULT_CORPORATION_ID,
)
from features.coloc.utils import (
    create_corporation_embed,
    create_corporation_logs_embed,
)

from ._common import logger


class CorporationMixin:
    """Daily corporation recap + manual /corpo trigger."""

    @Task.create(TimeTrigger(23, 59, 45, utc=False))
    async def corporation_recap(self, date: str | None = None):
        """Send daily corporation recap."""
        channel = await self._get_zunivers_channel()
        if not channel:
            return

        try:
            data = await self.api_client.get_corporation(DEFAULT_CORPORATION_ID)
            if not data:
                logger.warning("Could not fetch corporation data")
                return
        except ZuniversAPIError as e:
            await channel.send(f"Erreur lors de la récupération des données: {e}")
            return

        target_date = self._parse_date(date)
        if target_date is None:
            return

        logs = self._process_corporation_logs(data.get("corporationLogs", []), target_date)

        if not logs:
            return

        all_members = {m["user"]["discordGlobalName"] for m in data.get("userCorporations", [])}

        corp_embed = create_corporation_embed(data, CURRENCY_EMOJI)
        logs_embed = create_corporation_logs_embed(logs, all_members, target_date, CURRENCY_EMOJI)

        await channel.send(embeds=[corp_embed, logs_embed])

    @slash_command(
        name="corpo",
        description="Affiche les informations de la corporation",
        scopes=[668445729928249344],
    )
    @slash_option(
        name="date",
        description="Date du récap (YYYY-MM-DD)",
        opt_type=OptionType.STRING,
        required=False,
    )
    async def corpo_command(self, ctx: SlashContext, date: str | None = None):
        """Manual corporation recap command."""
        await self.corporation_recap(date=date)
        await ctx.send("Corporation recap envoyé !", ephemeral=True)

    def _parse_date(self, date_str: str | None = None) -> date_type | None:
        """Parse a date string or return today's date."""
        if date_str is None:
            return datetime.today().date()
        try:
            return datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            logger.warning(f"Invalid date format: {date_str}")
            return None

    def _process_corporation_logs(self, logs: list[dict], target_date: date_type) -> list[dict]:
        """Filter and process corporation logs for a specific date."""
        today_logs = []
        for log in logs:
            log_date = datetime.strptime(log["date"], "%Y-%m-%dT%H:%M:%S.%f").date()
            if log_date == target_date:
                today_logs.append(log)

        today_logs.sort(key=lambda x: datetime.strptime(x["date"], "%Y-%m-%dT%H:%M:%S.%f"))

        merged = []
        i = 0
        while i < len(today_logs):
            log = today_logs[i]
            merged_log = {
                "user_name": log["user"]["discordGlobalName"],
                "date": log["date"],
                "action": ACTION_TYPE_NAMES.get(log["action"], log["action"]),
                "amount": log.get("amount", 0),
            }

            if log["action"] == "UPGRADE":
                j = i + 1
                while j < len(today_logs) and today_logs[j]["date"] == log["date"]:
                    merged_log["amount"] += today_logs[j].get("amount", 0)
                    j += 1
                i = j
            else:
                i += 1

            merged.append(merged_log)

        return merged
