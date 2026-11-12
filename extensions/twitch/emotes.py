"""Twitch emote cache + change detection Task."""

import os

from interactions import File, OrTrigger, Task, TimeTrigger

from features.twitch import TwitchEmotesRepository
from features.twitch.emotes import diff_emotes, emote_image_url, emote_kind
from src.core import logging as logutil
from src.core.errors import DatabaseError
from src.core.http import http_client
from src.core.logging import log_failure
from src.discord_ext.embeds import Colors

from ._common import StreamerInfo

logger = logutil.init_logger(__name__)

EMOTE_CACHE_DIR = "data/emote_cache"


class EmotesMixin:
    """Detect new / replaced / deleted Twitch emotes and notify subscribed guilds."""

    async def download_emote_image(
        self,
        emote_id: str,
        image_url: str,
        streamer_id: str,
    ) -> str | None:
        """Download an emote PNG locally and return its path (or None on failure)."""
        if not image_url:
            return None

        file_path = os.path.join(EMOTE_CACHE_DIR, f"{streamer_id}_{emote_id}.png")

        try:
            session = await http_client.session()
            async with session.get(image_url) as response:
                if response.status == 200:
                    content = await response.read()
                    with open(file_path, "wb") as f:
                        f.write(content)
                    logger.debug(f"Cached emote image: {file_path}")
                    return file_path
                logger.error(f"Failed to download emote image {emote_id}: HTTP {response.status}")
                return None
        except Exception as e:
            log_failure(logger, f"Error downloading emote image {emote_id}: {e}")
            return None

    def get_cached_emote_path(self, emote_id: str, streamer_id: str) -> str | None:
        """Return the cached emote's path if it exists on disk, else None."""
        file_path = os.path.join(EMOTE_CACHE_DIR, f"{streamer_id}_{emote_id}.png")
        return file_path if os.path.exists(file_path) else None

    def delete_cached_emote(self, emote_id: str, streamer_id: str) -> None:
        """Remove a cached emote PNG if present."""
        file_path = os.path.join(EMOTE_CACHE_DIR, f"{streamer_id}_{emote_id}.png")
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.debug(f"Deleted cached emote: {file_path}")
        except Exception as e:
            log_failure(logger, f"Error deleting cached emote {emote_id}: {e}")

    @Task.create(OrTrigger(*[TimeTrigger(hour=i, utc=False) for i in range(24)]))
    async def check_new_emotes(self):
        """Compare each streamer's current Twitch emotes to the DB and notify diffs.

        Emotes are fetched once per streamer, however many guilds follow them,
        and each guild that opted in (``notifyEmoteChanges``) gets the news.
        """
        logger.debug("Checking new emotes")
        streamers_by_id: dict[str, list[StreamerInfo]] = {}
        for streamer in self.streamers.values():
            if streamer.user_id and streamer.notif_channel:
                streamers_by_id.setdefault(streamer.streamer_id, []).append(streamer)

        for streamer_id, guild_streamers in streamers_by_id.items():
            try:
                await self._check_streamer_emotes(streamer_id, guild_streamers)
            except Exception as e:  # noqa: BLE001 — one streamer must not stop the others
                log_failure(logger, f"Error checking emotes for {streamer_id}: {e}")

    async def _check_streamer_emotes(
        self, streamer_id: str, guild_streamers: list[StreamerInfo]
    ) -> None:
        emotes = await self.twitch.get_channel_emotes(guild_streamers[0].user_id)
        repo = TwitchEmotesRepository(streamer_id)
        try:
            stored = await repo.load_all()
        except DatabaseError as e:
            # Without the stored set every emote would look new: try next hour.
            logger.error(f"Error loading emotes from MongoDB for {streamer_id}: {e}")
            return

        if not stored:
            await self._initial_emote_sync(streamer_id, emotes, repo)
            return

        followers = [s for s in guild_streamers if s.notify_emote_changes]
        diff = diff_emotes(emotes, stored)
        for old_id, emote in diff.replaced:
            await self._on_emote_replaced(streamer_id, followers, old_id, emote, stored)
        for emote in diff.added:
            await self._on_emote_added(streamer_id, followers, emote, stored)
        for emote_id in diff.removed:
            await self._on_emote_removed(streamer_id, followers, emote_id, stored)
        backfilled = await self._backfill_emote_cache(streamer_id, emotes, stored)

        if diff or backfilled:
            await repo.replace_all(
                [
                    {"_id": eid, "name": edata["name"], "cached_file": edata.get("cached_file")}
                    for eid, edata in stored.items()
                ]
            )

    async def _initial_emote_sync(self, streamer_id: str, emotes, repo) -> None:
        """First check of a streamer: store their emotes without announcing any."""
        if not emotes:
            return
        logger.warning(
            "Initial emote sync for %s: %d emotes found – skipping notifications",
            streamer_id,
            len(emotes),
        )
        docs = []
        for emote in emotes:
            cached_file = await self.download_emote_image(
                emote.id, emote_image_url(emote.images), streamer_id
            )
            docs.append({"_id": emote.id, "name": emote.name, "cached_file": cached_file})
        await repo.insert_many(docs)

    async def _emote_embed(
        self,
        streamer: StreamerInfo,
        *,
        title: str,
        description: str,
        emote_name: str,
        last_field: tuple[str, str],
        color: int | None = None,
    ):
        """The notification embed shared by the three kinds of emote change."""
        kwargs = {"color": color} if color is not None else {}
        embed = await self.create_notification_embed(
            streamer.guild_id, title=title, description=description, **kwargs
        )
        embed.add_field(name="Emote", value=self.get_display_value(emote_name), inline=True)
        embed.add_field(
            name="Streamer", value=self.get_display_value(streamer.streamer_id), inline=True
        )
        embed.add_field(name=last_field[0], value=last_field[1], inline=True)
        return embed

    async def _on_emote_replaced(
        self, streamer_id: str, followers: list[StreamerInfo], old_id: str, emote, stored: dict
    ) -> None:
        """A re-uploaded emote: show the old and the new image side by side."""
        logger.info(f"Replaced emote for {streamer_id}: {emote.name}")
        old_cached_file = self.get_cached_emote_path(old_id, streamer_id)
        new_image_url = emote_image_url(emote.images)
        new_cached_file = await self.download_emote_image(emote.id, new_image_url, streamer_id)
        old_name, new_name = f"old_{emote.name}.png", f"new_{emote.name}.png"

        for streamer in followers:
            embed = await self._emote_embed(
                streamer,
                title="Mise à jour d'emote",
                description=(
                    f"L'emote **{emote.name}** a été remplacé sur la chaîne de **{streamer_id}**."
                ),
                emote_name=emote.name,
                last_field=("Action", "Remplacement"),
                color=Colors.ORANGE,
            )
            files: list[File] = []
            if old_cached_file and new_cached_file:
                # Multi-image trick: needs a shared embed url so Discord merges
                # the extra image embeds onto the main one.
                embed.url = f"https://twitch.tv/{streamer_id}"
                embed.set_images(f"attachment://{old_name}", f"attachment://{new_name}")
                files = [
                    File(old_cached_file, file_name=old_name),
                    File(new_cached_file, file_name=new_name),
                ]
            elif new_cached_file:
                embed.set_image(url=f"attachment://{new_name}")
                files = [File(new_cached_file, file_name=new_name)]
            elif new_image_url:
                embed.set_thumbnail(url=new_image_url)
            await streamer.notif_channel.send(embed=embed, files=files or None)

        if old_cached_file:
            self.delete_cached_emote(old_id, streamer_id)
        del stored[old_id]
        stored[emote.id] = {"name": emote.name, "cached_file": new_cached_file}

    async def _on_emote_added(
        self, streamer_id: str, followers: list[StreamerInfo], emote, stored: dict
    ) -> None:
        logger.info(f"New emote for {streamer_id}: {emote.name}")
        image_url = emote_image_url(emote.images)
        cached_file = await self.download_emote_image(emote.id, image_url, streamer_id)
        stored[emote.id] = {"name": emote.name, "cached_file": cached_file}
        kind = emote_kind(emote.emote_type, emote.tier)

        for streamer in followers:
            embed = await self._emote_embed(
                streamer,
                title="Nouvel emote ajouté",
                description=f"Un nouvel emote est disponible sur la chaine de **{streamer_id}**.",
                emote_name=emote.name,
                last_field=("Type", kind),
            )
            if image_url:
                embed.set_thumbnail(url=image_url)
            await streamer.notif_channel.send(embed=embed)

    async def _on_emote_removed(
        self, streamer_id: str, followers: list[StreamerInfo], emote_id: str, stored: dict
    ) -> None:
        emote_name = stored[emote_id]["name"]
        logger.info(f"Deleted emote for {streamer_id}: {emote_name}")
        cached_file = self.get_cached_emote_path(emote_id, streamer_id)
        attachment_name = f"{emote_name}.png"

        for streamer in followers:
            embed = await self._emote_embed(
                streamer,
                title="Emote supprimé",
                description=(
                    f"L'emote **{emote_name}** a été retiré de la chaîne de **{streamer_id}**."
                ),
                emote_name=emote_name,
                last_field=("Action", "Suppression"),
            )
            if cached_file:
                embed.set_thumbnail(url=f"attachment://{attachment_name}")
                await streamer.notif_channel.send(
                    embed=embed, files=[File(cached_file, file_name=attachment_name)]
                )
            else:
                await streamer.notif_channel.send(embed=embed)

        if cached_file:
            self.delete_cached_emote(emote_id, streamer_id)
        del stored[emote_id]

    async def _backfill_emote_cache(self, streamer_id: str, emotes, stored: dict) -> bool:
        """Download the images missing from the cache; whether any was."""
        backfilled = False
        for emote in emotes:
            if emote.id in stored and not self.get_cached_emote_path(emote.id, streamer_id):
                cached_file = await self.download_emote_image(
                    emote.id, emote_image_url(emote.images), streamer_id
                )
                if cached_file:
                    stored[emote.id]["cached_file"] = cached_file
                    backfilled = True
        return backfilled
