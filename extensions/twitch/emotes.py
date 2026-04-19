"""Twitch emote cache + change detection Task."""

import os

from interactions import File, OrTrigger, Task, TimeTrigger

from src.core import logging as logutil
from src.core.db import mongo_manager
from src.core.http import http_client
from src.discord_ext.embeds import Colors

from ._common import StreamerInfo

logger = logutil.init_logger(os.path.basename(__file__))

EMOTE_CACHE_DIR = "data/emote_cache"


class EmotesMixin:
    """Detect new / replaced / deleted Twitch emotes and notify subscribed guilds."""

    @staticmethod
    def get_emote_details(emote) -> str:
        """Return a normalized text label for the emote source/type."""
        if emote.emote_type == "subscriptions":
            tier = "1" if emote.tier == "1000" else "2" if emote.tier == "2000" else "3"
            return f"Sub tier {tier}"
        if emote.emote_type == "bitstier":
            return "Bits"
        if emote.emote_type == "follower":
            return "Follower"
        return "Autre"

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
            logger.error(f"Error downloading emote image {emote_id}: {e}")
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
            logger.error(f"Error deleting cached emote {emote_id}: {e}")

    @Task.create(OrTrigger(*[TimeTrigger(hour=i, utc=False) for i in range(24)]))
    async def check_new_emotes(self):
        """Compare each streamer's current Twitch emotes to the DB and notify diffs."""
        logger.debug("Checking new emotes")

        # Group streamers by streamer_id so we only fetch emotes once per unique streamer
        # and send notifications to all guilds following that streamer.
        streamers_by_id: dict[str, list[StreamerInfo]] = {}
        for streamer in self.streamers.values():
            if not streamer.user_id or not streamer.notif_channel:
                continue
            streamers_by_id.setdefault(streamer.streamer_id, []).append(streamer)

        for streamer_id, guild_streamers in streamers_by_id.items():
            user_id = guild_streamers[0].user_id

            try:
                emotes = await self.twitch.get_channel_emotes(user_id)
                emote_col = mongo_manager.get_global_collection(f"twitch_emotes_{streamer_id}")

                # Load existing emotes from MongoDB (global, shared across guilds).
                # Schema: {_id: emote_id, name: str, cached_file: str | None}
                data: dict[str, dict] = {}
                try:
                    async for doc in emote_col.find():
                        emote_id = doc["_id"]
                        data[emote_id] = {
                            "name": doc.get("name", ""),
                            "cached_file": doc.get("cached_file"),
                        }
                except Exception as e:
                    logger.error(f"Error loading emotes from MongoDB for {streamer_id}: {e}")

                new_emotes = [emote for emote in emotes if emote.id not in data]
                emote_ids = [emote.id for emote in emotes]
                deleted_emotes = [emote_id for emote_id in data if emote_id not in emote_ids]

                # If the DB was empty (initial sync after migration), skip all notifications
                # and just populate the database silently.
                if not data and new_emotes:
                    logger.warning(
                        "Initial emote sync for %s: %d emotes found – skipping notifications",
                        streamer_id,
                        len(new_emotes),
                    )
                    docs = []
                    for emote in emotes:
                        image_url = emote.images.get(
                            "url_4x", emote.images.get("url_2x", emote.images.get("url_1x"))
                        )
                        cached_file = await self.download_emote_image(
                            emote.id, image_url, streamer_id
                        )
                        docs.append(
                            {"_id": emote.id, "name": emote.name, "cached_file": cached_file}
                        )
                    if docs:
                        await emote_col.insert_many(docs)
                    continue

                # Detect replaced emotes (same name, different ID).
                current_emote_names = {emote.name: emote for emote in emotes}
                deleted_emote_names = {
                    data[emote_id]["name"]: emote_id for emote_id in deleted_emotes
                }

                replaced_emotes = []
                truly_new_emotes = []
                for emote in new_emotes:
                    if emote.name in deleted_emote_names:
                        replaced_emotes.append((deleted_emote_names[emote.name], emote))
                    else:
                        truly_new_emotes.append(emote)

                truly_deleted_emotes = [
                    emote_id
                    for emote_id in deleted_emotes
                    if data[emote_id]["name"] not in current_emote_names
                ]

                if replaced_emotes:
                    logger.info(f"Replaced emotes found for {streamer_id}")
                    for old_emote_id, new_emote in replaced_emotes:
                        old_cached_file = self.get_cached_emote_path(old_emote_id, streamer_id)
                        new_image_url = new_emote.images.get(
                            "url_4x",
                            new_emote.images.get("url_2x", new_emote.images.get("url_1x")),
                        )

                        logger.info(f"Replaced emote for {streamer_id}: {new_emote.name}")

                        for streamer in guild_streamers:
                            if not streamer.notify_emote_changes:
                                continue
                            embed = await self.create_notification_embed(
                                streamer.guild_id,
                                title="Mise à jour d'emote",
                                description=(
                                    f"L'emote **{new_emote.name}** a été remplacé sur la chaîne"
                                    f" de **{streamer_id}**."
                                ),
                                color=Colors.ORANGE,
                            )
                            embed.add_field(
                                name="Emote",
                                value=self.get_display_value(new_emote.name),
                                inline=True,
                            )
                            embed.add_field(
                                name="Streamer",
                                value=self.get_display_value(streamer_id),
                                inline=True,
                            )
                            embed.add_field(name="Action", value="Remplacement", inline=True)
                            if new_image_url:
                                embed.set_thumbnail(url=new_image_url)

                            if old_cached_file:
                                embed.add_field(
                                    name="Ancienne version", value="Image jointe", inline=True
                                )
                                embed.add_field(
                                    name="Nouvelle version",
                                    value="Thumbnail de l'embed",
                                    inline=True,
                                )
                                await streamer.notif_channel.send(
                                    embed=embed,
                                    files=[
                                        File(
                                            old_cached_file,
                                            file_name=f"old_{new_emote.name}.png",
                                        )
                                    ],
                                )
                            else:
                                embed.add_field(
                                    name="Nouvelle version",
                                    value="Thumbnail de l'embed",
                                    inline=True,
                                )
                                await streamer.notif_channel.send(embed=embed)

                        if old_cached_file:
                            self.delete_cached_emote(old_emote_id, streamer_id)

                        new_cached_file = await self.download_emote_image(
                            new_emote.id, new_image_url, streamer_id
                        )

                        del data[old_emote_id]
                        data[new_emote.id] = {
                            "name": new_emote.name,
                            "cached_file": new_cached_file,
                        }

                if truly_new_emotes:
                    logger.debug(f"New emotes found for {streamer_id}")
                    for emote in truly_new_emotes:
                        image_url = emote.images.get(
                            "url_4x", emote.images.get("url_2x", emote.images.get("url_1x"))
                        )

                        cached_file = await self.download_emote_image(
                            emote.id, image_url, streamer_id
                        )
                        data[emote.id] = {"name": emote.name, "cached_file": cached_file}

                        details = self.get_emote_details(emote)

                        logger.info(f"New emote for {streamer_id}: {emote.name}")

                        for streamer in guild_streamers:
                            if not streamer.notify_emote_changes:
                                continue
                            embed = await self.create_notification_embed(
                                streamer.guild_id,
                                title="Nouvel emote ajouté",
                                description=(
                                    f"Un nouvel emote est disponible sur la chaine de"
                                    f" **{streamer_id}**."
                                ),
                            )
                            embed.add_field(
                                name="Emote",
                                value=self.get_display_value(emote.name),
                                inline=True,
                            )
                            embed.add_field(
                                name="Streamer",
                                value=self.get_display_value(streamer_id),
                                inline=True,
                            )
                            embed.add_field(name="Type", value=details, inline=True)
                            if image_url:
                                embed.set_thumbnail(url=image_url)
                            await streamer.notif_channel.send(embed=embed)

                if truly_deleted_emotes:
                    logger.info(f"Deleted emotes found for {streamer_id}")
                    for emote_id in truly_deleted_emotes:
                        emote_data = data[emote_id]
                        emote_name = emote_data["name"]
                        cached_file = self.get_cached_emote_path(emote_id, streamer_id)

                        logger.info(f"Deleted emote for {streamer_id}: {emote_name}")

                        for streamer in guild_streamers:
                            if not streamer.notify_emote_changes:
                                continue
                            embed = await self.create_notification_embed(
                                streamer.guild_id,
                                title="Emote supprimé",
                                description=(
                                    f"L'emote **{emote_name}** a été retiré de la chaîne de"
                                    f" **{streamer_id}**."
                                ),
                            )
                            embed.add_field(
                                name="Emote",
                                value=self.get_display_value(emote_name),
                                inline=True,
                            )
                            embed.add_field(
                                name="Streamer",
                                value=self.get_display_value(streamer_id),
                                inline=True,
                            )
                            embed.add_field(name="Action", value="Suppression", inline=True)

                            if cached_file:
                                await streamer.notif_channel.send(
                                    embed=embed,
                                    files=[File(cached_file, file_name=f"{emote_name}.png")],
                                )
                            else:
                                await streamer.notif_channel.send(embed=embed)

                        if cached_file:
                            self.delete_cached_emote(emote_id, streamer_id)

                        del data[emote_id]

                # Backfill missing caches for existing emotes.
                for emote in emotes:
                    if emote.id in data and not self.get_cached_emote_path(emote.id, streamer_id):
                        image_url = emote.images.get(
                            "url_4x", emote.images.get("url_2x", emote.images.get("url_1x"))
                        )
                        cached_file = await self.download_emote_image(
                            emote.id, image_url, streamer_id
                        )
                        data[emote.id]["cached_file"] = cached_file

                has_cache_updates = any(
                    emote.id in data and data[emote.id].get("cached_file") is not None
                    for emote in emotes
                    if emote.id in data
                )
                if truly_new_emotes or truly_deleted_emotes or replaced_emotes or has_cache_updates:
                    await emote_col.delete_many({})
                    if data:
                        docs = [
                            {
                                "_id": eid,
                                "name": edata["name"],
                                "cached_file": edata.get("cached_file"),
                            }
                            for eid, edata in data.items()
                        ]
                        await emote_col.insert_many(docs)

            except Exception as e:
                logger.error(f"Error checking emotes for {streamer_id}: {e}")
