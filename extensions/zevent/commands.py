"""`/zevent_finish`: post the final recap embed once the event is over."""

import os

from interactions import SlashContext, slash_command

from src.core import logging as logutil
from src.core.http import fetch
from src.discord_ext.messages import edit_message_if_changed

from ._common import API_URL

logger = logutil.init_logger(os.path.basename(__file__))


class CommandsMixin:
    """Admin command to freeze the pinned message with the final recap."""

    @slash_command(name="zevent_finish", description="Créée l'embed final après l'évènement")
    async def end(self, ctx: SlashContext):
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
            await self.sync_scheduled_event(data, None, concert_active=False, finished=True)

            if self.message:
                await edit_message_if_changed(
                    self.message, embeds=embeds, content="", logger=logger
                )
                await ctx.send("Embed final créé avec succès", ephemeral=True)
            else:
                await ctx.send("Erreur: Message non trouvé", ephemeral=True)
        except Exception as e:
            logger.error(f"Error in zevent_finish command: {e}")
            await ctx.send("Erreur lors de la création de l'embed final", ephemeral=True)
