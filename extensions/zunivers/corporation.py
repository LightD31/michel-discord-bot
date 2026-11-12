"""CorporationMixin — daily corporation recap and /zunivers corpo command."""

from datetime import date as date_type
from datetime import datetime

from interactions import (
    OptionType,
    SlashContext,
    Task,
    TimeTrigger,
    slash_option,
)

from features.coloc.api_client import ZuniversAPIError
from features.coloc.constants import (
    ACTION_TYPE_NAMES,
    CURRENCY_EMOJI,
    DEFAULT_CORPORATION_ID,
)
from src.core.logging import log_failure
from src.discord_ext.messages import send_error

from ._common import logger, zunivers_api_url, zunivers_cmd, zunivers_web_url
from .embeds import create_corporation_embed, create_corporation_logs_embed


class CorporationMixin:
    """Daily corporation recap + manual /zunivers corpo trigger."""

    @Task.create(TimeTrigger(23, 59, 45, utc=False))
    async def corporation_recap(self):
        """Send the daily corporation recap to every enabled guild."""
        await self._send_corporation_recap(None)

    async def _send_corporation_recap(self, date: str | None, only: str | None = None) -> None:
        """Post the recap of *date* (today by default) in each guild's channel.

        The corporation is fetched once for every guild; *only* limits the post
        to one guild (the one running ``/zunivers corpo``).
        """
        channels = []
        for state, settings in await self._guilds():
            if only is not None and state.guild_id != only:
                continue
            channel = await self._zunivers_channel(settings)
            if channel:
                channels.append(channel)
        if not channels:
            return
        if not zunivers_api_url():
            logger.warning("No ZUnivers API URL (zunivers.apiUrl): skipping the corporation recap")
            return

        try:
            data = await self.api_client.get_corporation(DEFAULT_CORPORATION_ID)
            if not data:
                logger.warning("Could not fetch corporation data")
                return
        except ZuniversAPIError as e:
            # The API's own message (a URL, a status) means nothing in a public channel.
            logger.warning("Corporation recap: ZUnivers API error: %s", e)
            for channel in channels:
                await channel.send(
                    "⚠️ Récap de la corporation indisponible : l'API ZUnivers ne répond pas."
                )
            return

        target_date = self._parse_date(date)
        if target_date is None:
            return

        logs = self._process_corporation_logs(data.get("corporationLogs", []), target_date)

        if not logs:
            return

        all_members = {m["user"]["discordGlobalName"] for m in data.get("userCorporations", [])}

        corp_embed = create_corporation_embed(data, CURRENCY_EMOJI, zunivers_web_url())
        logs_embed = create_corporation_logs_embed(logs, all_members, target_date, CURRENCY_EMOJI)

        for channel in channels:
            try:
                await channel.send(embeds=[corp_embed, logs_embed])
            except Exception as e:  # noqa: BLE001 — one guild's failure must not stop the others
                log_failure(logger, "Could not post the corporation recap in %s: %s", channel.id, e)

    @zunivers_cmd.subcommand(
        sub_cmd_name="corpo",
        sub_cmd_description="Affiche les informations de la corporation",
    )
    @slash_option(
        name="date",
        description="Date du récap (YYYY-MM-DD)",
        opt_type=OptionType.STRING,
        required=False,
    )
    async def corpo_command(self, ctx: SlashContext, date: str | None = None):
        """Manual corporation recap command, posted in this guild's channel."""
        if not zunivers_api_url():
            await send_error(ctx, "L'API ZUnivers n'est pas configurée (section globale ZUnivers).")
            return
        await self._send_corporation_recap(date, only=str(ctx.guild_id))
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
