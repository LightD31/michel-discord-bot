"""StatusMixin — server status task, embed builders, channel renames, scheduled events."""

import asyncio
from datetime import datetime, timedelta

from interactions import (
    BaseChannel,
    BrandColors,
    Embed,
    Guild,
    IntervalTrigger,
    ScheduledEventStatus,
    ScheduledEventType,
    Task,
    Timestamp,
)
from mcstatus import JavaServer

from features.minecraft import MinecraftSettings
from src.discord_ext.messages import edit_message_if_changed

from ._common import MINECRAFT, build_stats_embed, logger


class StatusMixin:
    """Live-server status task and embed rendering."""

    @Task.create(IntervalTrigger(seconds=30))
    async def status(self):
        """Update each enabled guild's Minecraft server status every 30 seconds."""
        guilds = MINECRAFT.items()
        self._forget_disabled_guilds([guild_id for guild_id, _ in guilds])
        for guild_id, config in guilds:
            settings = MinecraftSettings.from_config(config)
            if not settings.has_server:
                logger.debug("Guild %s: no Minecraft server configured; skipping", guild_id)
                continue
            try:
                await self._update_status(guild_id, config, settings)
            except Exception as e:  # noqa: BLE001 — one server down must not stop the others
                logger.error("Guild %s: failed to update Minecraft server status: %s", guild_id, e)

    async def _server(self, settings: MinecraftSettings) -> JavaServer:
        # mcstatus' lookup and status are blocking socket calls: off the loop.
        try:
            return await asyncio.to_thread(JavaServer.lookup, settings.address)
        except Exception:
            logger.info("Could not find Minecraft server at %s using lookup", settings.address)
            return JavaServer(settings.ip, settings.port)

    async def _update_status(self, guild_id: str, config: dict, settings: MinecraftSettings):
        message = await self.status_messages.get(guild_id, config)
        if message is None:
            logger.debug("Guild %s: no status message configured yet; skipping", guild_id)
            return
        channel: BaseChannel = message.channel
        if guild_id not in self._events_recovered:
            await self._recover_scheduled_event(guild_id, channel)

        try:
            embed2_timestamp = message.embeds[1].timestamp
        except IndexError:
            embed2_timestamp = Timestamp.utcnow()

        embed2 = build_stats_embed(embed2_timestamp)

        server = await self._server(settings)
        players_online = 0
        try:
            coloc_status = await asyncio.to_thread(server.status)
            embed1, name = self._create_online_embed(coloc_status, settings)
            players_online = coloc_status.players.online

        except (ConnectionResetError, ConnectionRefusedError, TimeoutError) as e:
            logger.debug(e)
            embed1, name = self._create_offline_embed(settings)

        except BrokenPipeError:
            embed1, name = self._create_sleeping_embed(message, settings)

        await edit_message_if_changed(
            message, content="", embeds=[embed1, embed2], ignore_timestamp=True, logger=logger
        )
        await self._update_channel_name(guild_id, channel, name)
        await self._update_scheduled_event(guild_id, channel, players_online, settings)

    async def _recover_scheduled_event(self, guild_id: str, channel) -> None:
        """Pick up the event announcing players online that a previous run left."""
        self._events_recovered.add(guild_id)
        try:
            for event in await channel.guild.list_scheduled_events():
                creator = await event.creator
                if creator.id == self.bot.user.id and "Minecraft" in (event.name or ""):
                    self.scheduled_events[guild_id] = event
                    logger.info(f"Recovered existing Minecraft scheduled event: {event.name}")
                    break
        except Exception as e:
            logger.error(f"Failed to recover scheduled event: {e}")

    def _create_online_embed(self, coloc_status, settings: MinecraftSettings):
        """Create embed for online server status."""
        if coloc_status.players.online > 0:
            players = "\n".join(
                sorted(
                    [player.name.replace("_", r"\_") for player in coloc_status.players.sample],
                    key=str.lower,
                )
            )
            joueurs = f"Joueur{'s' if coloc_status.players.online > 1 else ''} ({coloc_status.players.online}/{coloc_status.players.max})"
        else:
            players = "\u200b"
            joueurs = "\u200b"

        embed = Embed(
            title=f"Serveur {settings.server_type + ' ' if settings.server_type else ''}{coloc_status.version.name}",
            description=f"Adresse : `{settings.address}`"
            + (
                f"\nModpack : [{settings.modpack_name}]({settings.modpack_url})"
                if settings.modpack_name and settings.modpack_url
                else ""
            )
            + (f"\nVersion : **{settings.modpack_version}**" if settings.modpack_version else ""),
            fields=[
                {
                    "name": "Latence",
                    "value": f"{coloc_status.latency:.2f} ms",
                    "inline": True,
                },
                {
                    "name": joueurs,
                    "value": players,
                    "inline": True,
                },
            ]
            + (
                [
                    {
                        "name": "État de Michel et du serveur",
                        "value": settings.status_url,
                    }
                ]
                if settings.status_url
                else []
            ),
            color=BrandColors.GREEN,
            timestamp=Timestamp.utcnow().isoformat(),
        )
        name = f"🟢︱{coloc_status.players.online if coloc_status.players.online != 0 else 'aucun'}᲼joueur{'s' if coloc_status.players.online > 1 else ''}"
        return embed, name

    def _create_offline_embed(self, settings: MinecraftSettings):
        """Create embed for offline server status."""
        embed = Embed(
            title="Serveur Hors-ligne",
            description=f"Adresse : `{settings.address}`",
            fields=[
                {
                    "name": "État de Michel et du serveur",
                    "value": settings.status_url,
                }
            ]
            if settings.status_url
            else [],
            color=BrandColors.RED,
            timestamp=Timestamp.utcnow().isoformat(),
        )
        return embed, "🔴︱hors-ligne"

    def _create_sleeping_embed(self, message, settings: MinecraftSettings):
        """Create embed for sleeping server status."""
        try:
            title = message.embeds[0].title
        except IndexError:
            title = "Serveur Minecraft"

        embed = Embed(
            title=title,
            description=f"Adresse : `{settings.address}`\n",
            fields=[
                {
                    "name": "Latence",
                    "value": "Serveur en veille :sleeping:",
                },
            ]
            + (
                [
                    {
                        "name": "État de Michel et du serveur",
                        "value": settings.status_url,
                    }
                ]
                if settings.status_url
                else []
            ),
            footer=settings.footer_text if settings.footer_text else None,
            timestamp=Timestamp.utcnow().isoformat(),
            color=BrandColors.YELLOW,
        )
        return embed, "🟡︱veille"

    async def _update_channel_name(self, guild_id: str, channel, name):
        """Update channel name if needed and not recently changed."""
        last_edit = self.channel_edit_timestamps.get(guild_id, datetime.fromtimestamp(0))
        if channel.name != name and last_edit < datetime.now() - timedelta(minutes=5):
            await channel.edit(name=name)
            self.channel_edit_timestamps[guild_id] = datetime.now()

    async def _update_scheduled_event(
        self, guild_id: str, channel, players_online, settings: MinecraftSettings
    ):
        """Create or delete a Discord scheduled event based on player count."""
        event = self.scheduled_events.get(guild_id)
        try:
            guild: Guild = channel.guild
            if players_online > 0:
                if not event:
                    event_name = f"Minecraft - {players_online} joueur{'s' if players_online > 1 else ''} en ligne"
                    event = await guild.create_scheduled_event(
                        name=event_name,
                        event_type=ScheduledEventType.EXTERNAL,
                        external_location=f"Serveur Minecraft : {settings.address}",
                        start_time=datetime.now().astimezone() + timedelta(seconds=5),
                        end_time=datetime.now().astimezone() + timedelta(days=1),
                        description=f"Des joueurs sont connectés sur le serveur Minecraft !\nAdresse : `{settings.address}`",
                    )
                    self.scheduled_events[guild_id] = event
                    await event.edit(status=ScheduledEventStatus.ACTIVE)
                    logger.info(f"Created Minecraft scheduled event: {event_name}")
                else:
                    event_name = f"Minecraft - {players_online} joueur{'s' if players_online > 1 else ''} en ligne"
                    if event.name != event_name:
                        await event.edit(
                            name=event_name,
                            end_time=datetime.now().astimezone() + timedelta(days=1),
                        )
                        logger.debug(f"Updated Minecraft scheduled event: {event_name}")
            else:
                if event:
                    await event.delete()
                    self.scheduled_events.pop(guild_id, None)
                    logger.info("Deleted Minecraft scheduled event (no players online)")
        except Exception as e:
            logger.error(f"Failed to update scheduled event: {e}")
            self.scheduled_events.pop(guild_id, None)
