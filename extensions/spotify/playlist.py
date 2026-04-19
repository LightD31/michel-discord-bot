"""Playlist-centric slash commands, autocompletes, and periodic sync tasks."""

import os
import random
from datetime import datetime

import pytz
import spotipy
from interactions import (
    AutocompleteContext,
    Embed,
    IntervalTrigger,
    OptionType,
    SlashContext,
    Task,
    Timestamp,
    TimestampStyles,
    TimeTrigger,
    slash_command,
    slash_option,
)
from interactions.client.utils import timestamp_converter

from dict import finishList, startList
from src import logutil
from src.helpers import Colors, fetch_or_create_persistent_message, send_error
from src.integrations.spotify import spotifymongoformat
from src.utils import milliseconds_to_string

from ._common import (
    SERVERS,
    EmbedType,
    ServerData,
    count_votes,
    embed_message_vote,
    embed_song,
    enabled_servers,
    sp,
)

logger = logutil.init_logger(os.path.basename(__file__))


class PlaylistMixin:
    """Slash commands + periodic tasks for playlist adds, info, and sync."""

    @slash_command(
        "addsong",
        description="Ajoute une chanson à la playlist.",
        scopes=enabled_servers,
    )
    @slash_option(
        name="song",
        description="Nom de la chanson à ajouter",
        opt_type=OptionType.STRING,
        required=True,
        autocomplete=True,
    )
    async def addsong(self, ctx: SlashContext, song):
        server = self.get_server(ctx.guild_id)
        if str(ctx.channel_id) != str(server.channel_id):
            await send_error(ctx, "Vous ne pouvez pas utiliser cette commande dans ce salon.")
            logger.info("Commande /addsong utilisée dans un mauvais salon(%s)", ctx.channel.name)
            return

        logger.info(
            "/addsong '%s' utilisé par %s(id:%s)",
            song,
            ctx.author.username,
            ctx.author_id,
        )

        try:
            track = sp.track(song, market="FR")
            song_data = spotifymongoformat(
                track, ctx.author_id, spotify2discord=server.spotify2discord
            )
        except spotipy.exceptions.SpotifyException:
            await send_error(ctx, "Cette chanson n'existe pas.")
            logger.info("Commande /addsong utilisée avec une chanson inexistante")
            return

        existing_ids = await server.playlist_items_full.distinct("_id")
        if song_data["_id"] not in existing_ids:
            await server.playlist_items_full.insert_one(song_data)
            sp.playlist_add_items(server.playlist_id, [song_data["_id"]])
            embed, file = await embed_song(
                song=song_data,
                track=track,
                embedtype=EmbedType.ADD,
                time=Timestamp.utcnow(),
                person=ctx.author.username,
                icon=ctx.author.avatar.url,
            )
            await ctx.send(
                content=(
                    f"{random.choice(startList)} {ctx.author.mention}, {random.choice(finishList)}"
                ),
                embeds=embed,
                files=[file] if file else None,
            )
            logger.info("%s ajouté par %s", track["name"], ctx.author.display_name)
        else:
            await send_error(ctx, "Cette chanson a déjà été ajoutée à la playlist.")
            logger.info("Commande /addsong utilisée avec une chanson déjà présente")

    @addsong.autocomplete("song")
    async def autocomplete_from_spotify(self, ctx: AutocompleteContext):
        if not ctx.input_text:
            choices = [{"name": "Veuillez entrer un nom de chanson", "value": "error"}]
        else:
            items = sp.search(ctx.input_text, limit=10, type="track", market="FR")["tracks"][
                "items"
            ]
            if not items:
                choices = [{"name": "Aucun résultat", "value": "error"}]
            else:
                choices = [
                    {
                        "name": (
                            f"{item['artists'][0]['name']} - {item['name']} "
                            f"(Album: {item['album']['name']})"
                        )[:100],
                        "value": item["uri"],
                    }
                    for item in items
                ]
        await ctx.send(choices=choices)

    @Task.create(IntervalTrigger(minutes=1, seconds=0))
    async def check_playlist_changes(self):
        """Reconcile MongoDB with the live Spotify playlist, sending adds/removes."""
        for server in SERVERS.values():
            try:
                await self._check_playlist_changes_for_server(server)
            except Exception as e:
                logger.error(
                    "Error in check_playlist_changes for server %s: %s",
                    server.guild_id,
                    e,
                )

    async def _check_playlist_changes_for_server(self, server: ServerData):
        logger.debug("check_playlist_changes lancé pour le serveur %s", server.guild_id)

        channel = await self.bot.fetch_channel(server.channel_id)
        logger.debug(
            "old_snap : %s, duration : %s, length : %s",
            server.snapshot.get("snapshot"),
            server.snapshot.get("duration"),
            server.snapshot.get("length"),
        )
        try:
            new_snap = sp.playlist(server.playlist_id, fields="snapshot_id")["snapshot_id"]
        except spotipy.SpotifyException as e:
            logger.error("Spotify API Error : %s", e)
            return
        except ConnectionError as e:
            logger.error("ConnectionError : %s", e)
            return

        if new_snap != server.snapshot.get("snapshot"):
            try:
                results = sp.playlist_tracks(playlist_id=server.playlist_id, limit=100, offset=0)
            except spotipy.SpotifyException as e:
                logger.error("Spotify API Error : %s", e)
                return
            tracks = results["items"]
            while results["next"]:
                results = sp.next(results)
                tracks.extend(results["items"])

            length = len(tracks)
            duration = 0
            last_track_ids = await server.playlist_items_full.distinct("_id")
            current_track_ids = {track["track"]["id"] for track in tracks}
            added_track_ids = list(set(current_track_ids) - set(last_track_ids))
            removed_track_ids = list(set(last_track_ids) - set(current_track_ids))
            logger.debug("added_track_ids : %s", added_track_ids)
            logger.debug("removed_track_ids : %s", removed_track_ids)

            skip_notifications = not last_track_ids
            if skip_notifications:
                logger.warning(
                    "Initial playlist sync for server %s: %d tracks – skipping notifications",
                    server.guild_id,
                    len(added_track_ids),
                )

            for track in tracks:
                song = spotifymongoformat(track, spotify2discord=server.spotify2discord)
                duration += track["track"]["duration_ms"]
                if track["track"]["id"] in added_track_ids:
                    song = spotifymongoformat(track, spotify2discord=server.spotify2discord)
                    await server.playlist_items_full.insert_one(song)
                    if not skip_notifications:
                        track = sp.track(track["track"]["id"], market="FR")
                        dt = timestamp_converter(
                            datetime.fromisoformat(song["added_at"]).astimezone(
                                pytz.timezone("Europe/Paris")
                            )
                        )
                        embed, file = await embed_song(
                            song=song,
                            track=track,
                            embedtype=EmbedType.ADD,
                            time=dt,
                            person=server.discord2name.get(song["added_by"], song["added_by"]),
                        )
                        await channel.send(
                            content=(
                                f"{random.choice(startList)} <@{song['added_by']}>, "
                                f"{random.choice(finishList)}\n"
                                f"{track['external_urls']['spotify']}"
                            ),
                            embeds=embed,
                            files=[file] if file else None,
                        )
                        logger.info(
                            "%s ajouté par %s",
                            track["name"],
                            server.discord2name.get(song["added_by"], song["added_by"]),
                        )
            if removed_track_ids:
                logger.info(
                    "%s chanson(s) ont été supprimée(s) depuis la dernière vérification",
                    len(removed_track_ids),
                )
                for track_id in removed_track_ids:
                    song = await server.playlist_items_full.find_one_and_delete({"_id": track_id})
                    if not skip_notifications:
                        track = sp.track(track_id, market="FR")
                        embed, file = await embed_song(
                            song=song,
                            track=track,
                            embedtype=EmbedType.DELETE,
                            time=Timestamp.utcnow(),
                        )
                        channel = await self.bot.fetch_channel(server.channel_id)
                        await channel.send(
                            track["external_urls"]["spotify"],
                            embeds=embed,
                            files=[file] if file else None,
                        )

            server.snapshot["snapshot"] = new_snap
            server.snapshot["length"] = length
            server.snapshot["duration"] = duration
            await self.save_snapshot(server)
            logger.debug("Snapshot mis à jour")

        if server.recap_channel_id:
            recap_content = (
                f"Dernière màj de la playlist "
                f"{Timestamp.utcnow().format(TimestampStyles.RelativeTime)}, "
                f"si c'était il y a plus d'**une minute**, il y a probablement un problème\n"
                f"`/addsong Titre et artiste de la chanson` pour ajouter une chanson\n"
                f"Il y a actuellement **{server.snapshot.get('length', 0)}** chansons dans la "
                f"playlist, pour un total de "
                f"**{milliseconds_to_string(server.snapshot.get('duration', 0))}**\n"
                f"Dashboard : https://drndvs.link/StatsPlaylist"
            )
            try:
                if server.recap_message is None:
                    server.recap_message = await fetch_or_create_persistent_message(
                        self.bot,
                        channel_id=server.recap_channel_id,
                        message_id=server.recap_message_id,
                        module_name="moduleSpotify",
                        message_id_key="spotifyRecapMessageId",
                        guild_id=server.guild_id,
                        initial_content=recap_content,
                        pin=server.recap_pin,
                        logger=logger,
                    )
                if server.recap_message is not None:
                    await server.recap_message.edit(content=recap_content)
            except Exception as e:
                logger.error("Error while trying to edit recap message: %s", e)

    @slash_command(
        name="songinfo",
        description="Affiche les informations d'une chanson",
        scopes=enabled_servers,
    )
    @slash_option(
        name="song",
        description="Nom de la chanson",
        opt_type=OptionType.STRING,
        required=True,
        autocomplete=True,
        argument_name="song_id",
    )
    async def songinfo(self, ctx: SlashContext, song_id):
        """Show combined MongoDB + Spotify info (and any vote history) for a song."""
        server = self.get_server(ctx.guild_id)
        embed = None
        song = await server.playlist_items_full.find_one({"_id": song_id})
        votes = await server.votes_db.find_one({"_id": song_id})
        track = sp.track(song_id, market="FR")
        if song:
            embed, file = await embed_song(
                song=song,
                track=track,
                embedtype=EmbedType.INFOS,
                time=song["added_at"],
                person=song["added_by"],
            )
        else:
            song = spotifymongoformat(
                track, votes.get("added_by", "Inconnu"), spotify2discord=server.spotify2discord
            )
            embed, file = await embed_song(
                song=song,
                track=track,
                embedtype=EmbedType.INFOS,
                time=Timestamp.utcnow(),
                person=votes.get("added_by", "Inconnu"),
            )
        if votes:
            if votes.get("votes"):
                conserver, supprimer, menfou, users = count_votes(
                    votes.get("votes", {}), server.discord2name
                )
                date = votes.get("date")
                if date:
                    date = timestamp_converter(datetime.strptime(date, "%Y-%m-%d")).format(
                        TimestampStyles.LongDate
                    )
                embeds = [
                    embed,
                    await embed_message_vote(
                        keep=conserver,
                        remove=supprimer,
                        menfou=menfou,
                        users=users,
                        color=Colors.SPOTIFY,
                        description=(
                            f"Vote effectué le {date}\n"
                            f"La chanson a été **{votes.get('state', '')}**"
                        ),
                    ),
                ]
            else:
                embeds = [
                    embed,
                    Embed(
                        title="Vote",
                        description=(
                            f"La chanson est passée au vote et a été "
                            f"**{votes.get('state', '')}**\nPas de détails sur le vote."
                        ),
                        color=Colors.SPOTIFY,
                    ),
                ]
        else:
            embeds = [embed]
        await ctx.send(embeds=embeds, files=[file] if file else None)
        if not song and not votes:
            await send_error(ctx, "Cette chanson n'existe pas.")

    @songinfo.autocomplete("song")
    async def autocomplete_from_db(self, ctx: AutocompleteContext):
        """Match name/artist against the local MongoDB playlist + vote archives."""
        server = self.get_server(ctx.guild_id)
        if not ctx.input_text:
            choices = [{"name": "Veuillez entrer un nom de chanson", "value": "error"}]
        else:
            words = ctx.input_text.split()
            regex_pattern = "|".join(words)

            query = {
                "$or": [
                    {"name": {"$regex": regex_pattern, "$options": "i"}},
                    {"artists": {"$regex": regex_pattern, "$options": "i"}},
                ]
            }
            playlist_items = {
                item["_id"]: item async for item in server.playlist_items_full.find(query)
            }
            votes = {item["_id"]: item async for item in server.votes_db.find(query)}

            results = {**playlist_items, **votes}
            if not results:
                choices = [{"name": "Aucun résultat", "value": "error"}]
            else:
                choices = [
                    {
                        "name": (
                            f"{', '.join(result['artists'])} - {result['name']}"
                            if result.get("artists")
                            else f"{result['name']}"
                        )[:100],
                        "value": result["_id"],
                    }
                    for _songresult_id, result in results.items()
                ]
                logger.debug("choices : %s", choices)
        await ctx.send(choices=choices[0:25])

    @Task.create(TimeTrigger(hour=4, minute=30, utc=False))
    async def new_titles_playlist(self):
        """Rebuild the ‘découvertes’ playlist: 100 most-recent distinct-artist tracks."""
        for server in SERVERS.values():
            try:
                await self._new_titles_playlist_for_server(server)
            except Exception as e:
                logger.error(
                    "Error in new_titles_playlist for server %s: %s",
                    server.guild_id,
                    e,
                )

    async def _new_titles_playlist_for_server(self, server: ServerData):
        logger.debug("new_titles_playlist lancé pour le serveur %s", server.guild_id)

        results = sp.playlist_tracks(playlist_id=server.playlist_id, limit=100, offset=0)
        tracks = results["items"]

        while results["next"]:
            results = sp.next(results)
            tracks.extend(results["items"])

        tracks.reverse()

        new_tracks = []
        artists = set()
        for i, track in enumerate(tracks, 1):
            track_artists = {artist["name"] for artist in track["track"]["artists"]}

            if not artists.intersection(track_artists):
                new_tracks.append(track["track"]["id"])
                artists.update(track_artists)

            if len(new_tracks) >= 100:
                logger.info(
                    "Playlist 'Les découvertes' créée à partir de %s titres",
                    i,
                )
                break

        sp.playlist_replace_items(server.new_playlist_id, new_tracks)
