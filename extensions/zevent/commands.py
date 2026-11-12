"""`/zevent_finish`: post the final recap embed once the event is over."""

import os

from interactions import SlashContext, slash_command

from src.core import logging as logutil
from src.core.http import fetch
from src.core.logging import log_failure
from src.discord_ext.messages import edit_message_if_changed
from src.discord_ext.scopes import guild_scopes

from ._common import API_URL, ZEVENT, ZeventGuild

logger = logutil.init_logger(__name__)


class CommandsMixin:
    """Admin command to freeze the guild's tracker message with the final recap."""

    @slash_command(
        name="zevent_finish",
        description="Créée l'embed final après l'évènement",
        scopes=guild_scopes(ZEVENT.guild_ids()),
    )
    async def end(self, ctx: SlashContext):
        settings = ZEVENT.get(ctx.guild_id)
        if not settings:
            await ctx.send("Le module Zevent n'est pas activé sur ce serveur.", ephemeral=True)
            return
        if not API_URL:
            await ctx.send(
                "L'API Zevent n'est pas configurée (section globale Zevent).", ephemeral=True
            )
            return
        guild_id = str(ctx.guild_id)
        guild = self.guilds.setdefault(guild_id, ZeventGuild(guild_id))
        try:
            data = await fetch(API_URL, return_type="json")
            if not data or not self._validate_api_data(data, "zevent"):
                await ctx.send(
                    "Erreur: Impossible de récupérer les données du Zevent", ephemeral=True
                )
                return

            total_amount = self._safe_get_data(
                data, ["donationAmount", "formatted"], "Données indisponibles"
            )
            streams = await self.categorize_streams(self._safe_get_data(data, ["live"], []))

            embeds = [
                self.create_main_embed(total_amount, finished=True),
                self.create_location_embed(
                    "streamers présents sur place",
                    streams["LAN"],
                    finished=True,
                    withlink=False,
                    total_count=self._get_stream_total_count(streams, "LAN"),
                ),
                self.create_location_embed(
                    "participants à distance",
                    streams["Online"],
                    finished=True,
                    withlink=False,
                    total_count=self._get_stream_total_count(streams, "Online"),
                ),
            ]

            embeds = self.ensure_embeds_fit_limit(embeds)

            # The recap freezes the tracker, so the guild's scheduled event has
            # nothing left to announce either.
            await self.sync_scheduled_event(
                guild, settings, data, None, concert_active=False, finished=True
            )

            message = await self.messages.get(guild_id, settings)
            if message:
                await edit_message_if_changed(message, embeds=embeds, content="", logger=logger)
                await ctx.send("Embed final créé avec succès", ephemeral=True)
            else:
                await ctx.send("Erreur: Message non trouvé", ephemeral=True)
        except Exception as e:
            log_failure(logger, f"Error in zevent_finish command: {e}")
            await ctx.send("Erreur lors de la création de l'embed final", ephemeral=True)
