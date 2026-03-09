"""
EmbedManager Extension - Custom Embed Publisher

This extension allows admins to create and manage embeds with arbitrary
links through the web UI. Embeds are configured in the per-guild module config
and published to a designated channel message.

Features:
- Create embeds with custom titles and colors
- Add multiple links with titles to each embed
- Automatically publish embeds to a configured message
- Edit embeds through the web UI
"""

import os

from interactions import (
    Client,
    Embed,
    Extension,
    SlashContext,
    slash_command,
)

from src import logutil
from src.utils import load_config

logger = logutil.init_logger(os.path.basename(__file__))
config, module_config, enabled_servers = load_config("moduleEmbedManager")


class EmbedManager(Extension):
    """EmbedManager extension for publishing custom embeds with links."""

    def __init__(self, bot: Client):
        self.bot = bot

    @slash_command(
        name="embeds",
        description="Gestion des embeds personnalisés",
        sub_cmd_name="publish",
        sub_cmd_description="Publier les embeds configurés",
    )
    async def publish_embeds(self, ctx: SlashContext):
        """Publish configured embeds to the target message."""
        try:
            guild_id = ctx.guild_id
            
            if guild_id not in enabled_servers:
                await ctx.send("✗ Ce module n'est pas activé sur ce serveur.", ephemeral=True)
                return

            guild_config = module_config.get(guild_id, {})
            if not guild_config.get("enabled"):
                await ctx.send("✗ Le module EmbedManager n'est pas activé sur ce serveur.", ephemeral=True)
                return

            channel_id = guild_config.get("channelId")
            message_id = guild_config.get("messageId")
            embeds_config = guild_config.get("embeds", [])

            if not channel_id or not message_id:
                await ctx.send("✗ Le salon ou le message cible n'est pas configuré.", ephemeral=True)
                return

            if not embeds_config:
                await ctx.send("✗ Aucun embed n'a été configuré.", ephemeral=True)
                return

            # Build Discord embeds from config
            discord_embeds = self._build_embeds(embeds_config)

            if not discord_embeds:
                await ctx.send("✗ Erreur lors de la génération des embeds.", ephemeral=True)
                return

            # Fetch and edit the message
            try:
                channel = await self.bot.fetch_channel(int(channel_id))
                message = await channel.fetch_message(int(message_id))
                await message.edit(embeds=discord_embeds)
                await ctx.send(f"✓ Embeds publiés avec succès ({len(discord_embeds)} embed(s))!", ephemeral=True)
            except Exception as e:
                logger.error(f"Failed to update message in channel {channel_id}: {e}")
                await ctx.send(f"✗ Erreur lors de la mise à jour du message: {e}", ephemeral=True)

        except Exception as e:
            logger.error(f"Error in publish_embeds: {e}", exc_info=True)
            await ctx.send(f"✗ Erreur: {e}", ephemeral=True)

    def _build_embeds(self, embeds_config: list[dict]) -> list[Embed]:
        """Build Discord embeds from configuration.
        
        Args:
            embeds_config: List of embed configurations, each with:
                - title: str (embed title)
                - color: str (hex color code without #)
                - links: list[dict] (optional, list of {title, url})
        
        Returns:
            List of Embed objects
        """
        embeds = []
        
        for embed_data in embeds_config:
            if not isinstance(embed_data, dict):
                logger.warning(f"Invalid embed config: {embed_data}")
                continue
                
            title = embed_data.get("title", "Sans titre")
            color_hex = embed_data.get("color", "3498db").lstrip("#")
            
            # Convert hex color to integer
            try:
                color_int = int(color_hex, 16)
            except ValueError:
                logger.warning(f"Invalid color format: {color_hex}, using default")
                color_int = int("3498db", 16)
            
            embed = Embed(title=title, color=color_int)

            # Add links as fields
            links = embed_data.get("links", [])
            if links and isinstance(links, list):
                for link_data in links:
                    if not isinstance(link_data, dict):
                        continue
                    link_title = link_data.get("title", "Lien")
                    link_url = link_data.get("url", "")
                    
                    if link_url:
                        embed.add_field(
                            name=link_title,
                            value=f"[Cliquez ici]({link_url})",
                            inline=False,
                        )
            
            embeds.append(embed)

        return embeds


def setup(bot: Client):
    """Load the extension."""
    EmbedManager(bot)
