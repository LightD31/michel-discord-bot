"""StatusMixin — server status task, embed builders, channel renames, scheduled events."""

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
    TimestampStyles,
)
from mcstatus import JavaServer

from ._common import (
    FOOTER_TEXT,
    MINECRAFT_ADDRESS,
    MINECRAFT_IP,
    MINECRAFT_PORT,
    MODPACK_NAME,
    MODPACK_URL,
    MODPACK_VERSION,
    SERVER_TYPE,
    STATUS_URL,
    logger,
)


class StatusMixin:
    """Live-server status task and embed rendering."""

    @Task.create(IntervalTrigger(seconds=30))
    async def status(self):
        """Update the Minecraft server status every 30 seconds."""
        try:
            self.serverColoc = JavaServer.lookup(MINECRAFT_ADDRESS)
        except Exception:
            logger.info("Could not find Minecraft server at %s using lookup", MINECRAFT_ADDRESS)
            self.serverColoc = JavaServer(MINECRAFT_IP, MINECRAFT_PORT)

        logger.debug("Updating Minecraft server status")
        try:
            message = await self._get_status_message()
            if message is None:
                logger.debug("No status message configured yet; skipping")
                return
            channel: BaseChannel = message.channel

            try:
                embed2_timestamp = message.embeds[1].timestamp
            except IndexError:
                embed2_timestamp = Timestamp.utcnow()

            embed2 = Embed(
                title="Stats",
                description=f"Actualisé toutes les heures\nDernière actualisation : {embed2_timestamp.format(TimestampStyles.RelativeTime)}",
                images=("attachment://stats.png"),
                color=BrandColors.BLURPLE,
                timestamp=embed2_timestamp,
            )

            players_online = 0
            try:
                coloc_status = self.serverColoc.status()
                embed1, name = self._create_online_embed(coloc_status)
                players_online = coloc_status.players.online

            except (ConnectionResetError, ConnectionRefusedError, TimeoutError) as e:
                logger.debug(e)
                embed1, name = self._create_offline_embed()

            except BrokenPipeError:
                embed1, name = self._create_sleeping_embed(message)

            await message.edit(content="", embeds=[embed1, embed2])
            await self._update_channel_name(channel, name)
            await self._update_scheduled_event(channel, players_online)
        except Exception as e:
            logger.error(f"Failed to update Minecraft server status: {e}")

    def _create_online_embed(self, coloc_status):
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
            title=f"Serveur {SERVER_TYPE + ' ' if SERVER_TYPE else ''}{coloc_status.version.name}",
            description=f"Adresse : `{MINECRAFT_ADDRESS}`"
            + (
                f"\nModpack : [{MODPACK_NAME}]({MODPACK_URL})"
                if MODPACK_NAME and MODPACK_URL
                else ""
            )
            + (f"\nVersion : **{MODPACK_VERSION}**" if MODPACK_VERSION else ""),
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
                        "value": STATUS_URL,
                    }
                ]
                if STATUS_URL
                else []
            ),
            color=BrandColors.GREEN,
            timestamp=Timestamp.utcnow().isoformat(),
        )
        name = f"🟢︱{coloc_status.players.online if coloc_status.players.online != 0 else 'aucun'}᲼joueur{'s' if coloc_status.players.online > 1 else ''}"
        return embed, name

    def _create_offline_embed(self):
        """Create embed for offline server status."""
        embed = Embed(
            title="Serveur Hors-ligne",
            description=f"Adresse : `{MINECRAFT_ADDRESS}`",
            fields=[
                {
                    "name": "État de Michel et du serveur",
                    "value": STATUS_URL,
                }
            ]
            if STATUS_URL
            else [],
            color=BrandColors.RED,
            timestamp=Timestamp.utcnow().isoformat(),
        )
        return embed, "🔴︱hors-ligne"

    def _create_sleeping_embed(self, message):
        """Create embed for sleeping server status."""
        try:
            title = message.embeds[0].title
        except IndexError:
            title = "Serveur Minecraft"

        embed = Embed(
            title=title,
            description=f"Adresse : `{MINECRAFT_ADDRESS}`\n",
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
                        "value": STATUS_URL,
                    }
                ]
                if STATUS_URL
                else []
            ),
            footer=FOOTER_TEXT if FOOTER_TEXT else None,
            timestamp=Timestamp.utcnow().isoformat(),
            color=BrandColors.YELLOW,
        )
        return embed, "🟡︱veille"

    async def _update_channel_name(self, channel, name):
        """Update channel name if needed and not recently changed."""
        if channel.name != name and self.channel_edit_timestamp < datetime.now() - timedelta(
            minutes=5
        ):
            await channel.edit(name=name)
            self.channel_edit_timestamp = datetime.now()

    async def _update_scheduled_event(self, channel, players_online):
        """Create or delete a Discord scheduled event based on player count."""
        try:
            guild: Guild = channel.guild
            if players_online > 0:
                if not self.scheduled_event:
                    event_name = f"Minecraft - {players_online} joueur{'s' if players_online > 1 else ''} en ligne"
                    self.scheduled_event = await guild.create_scheduled_event(
                        name=event_name,
                        event_type=ScheduledEventType.EXTERNAL,
                        external_location=f"Serveur Minecraft : {MINECRAFT_ADDRESS}",
                        start_time=datetime.now().astimezone() + timedelta(seconds=5),
                        end_time=datetime.now().astimezone() + timedelta(days=1),
                        description=f"Des joueurs sont connectés sur le serveur Minecraft !\nAdresse : `{MINECRAFT_ADDRESS}`",
                    )
                    await self.scheduled_event.edit(status=ScheduledEventStatus.ACTIVE)
                    logger.info(f"Created Minecraft scheduled event: {event_name}")
                else:
                    event_name = f"Minecraft - {players_online} joueur{'s' if players_online > 1 else ''} en ligne"
                    if self.scheduled_event.name != event_name:
                        await self.scheduled_event.edit(
                            name=event_name,
                            end_time=datetime.now().astimezone() + timedelta(days=1),
                        )
                        logger.debug(f"Updated Minecraft scheduled event: {event_name}")
            else:
                if self.scheduled_event:
                    await self.scheduled_event.delete()
                    self.scheduled_event = None
                    logger.info("Deleted Minecraft scheduled event (no players online)")
        except Exception as e:
            logger.error(f"Failed to update scheduled event: {e}")
            self.scheduled_event = None
