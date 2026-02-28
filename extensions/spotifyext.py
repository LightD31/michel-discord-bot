import json
import os
import random
import time
from datetime import datetime, timedelta

import interactions
import pymongo
import pytz
import aiohttp
import spotipy
from interactions.api.events import Component

from dict import finishList, startList
from src import logutil
from src.spotify import (
    EmbedType,
    count_votes,
    embed_message_vote,
    embed_song,
    spotify_auth,
    spotifymongoformat,
    embed_message_vote_add,
)
from src.utils import milliseconds_to_string, load_config

# Constants and Configuration
CONFIG, MODULE_CONFIGS, ENABLED_SERVERS = load_config("moduleSpotify")

SPOTIFY_CLIENT_ID = CONFIG["spotify"]["spotifyClientId"]
SPOTIFY_CLIENT_SECRET = CONFIG["spotify"]["spotifyClientSecret"]
SPOTIFY_REDIRECT_URI = CONFIG["spotify"]["spotifyRedirectUri"]
DEV_GUILD = CONFIG["discord"]["devGuildId"]
COOLDOWN_TIME = 1
DATA_FOLDER = CONFIG["misc"]["dataFolder"]

# Logger setup
logger = logutil.init_logger(os.path.basename(__file__))

# MongoDB setup
mongo_client = pymongo.MongoClient(CONFIG["mongodb"]["url"])

# Spotify authentication
sp = spotify_auth()

# Global variables
last_votes = {}


class VoteManager:
    def __init__(self, file_path, discord2name):
        self.file_path = file_path
        self.discord2name = discord2name
        if not os.path.exists(file_path):
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump({}, f)

    def load_data(self):
        with open(self.file_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_data(self, data):
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

    def count_votes(self, data, song):
        song_data = data[song]
        yes_votes = sum(1 for v in song_data["votes"].values() if v == "yes")
        no_votes = sum(1 for v in song_data["votes"].values() if v == "no")
        users = [self.discord2name.get(user, f"<@{user}>") for user in song_data["votes"]]
        return yes_votes, no_votes, users

    def check_deadline(self, song_id):
        data = self.load_data()
        return float(data[song_id]["deadline"]) <= datetime.now().timestamp()

    def save_vote(self, author_id, vote, song):
        data = self.load_data()
        logger.info("%s voted %s to add %s", author_id, vote, song)
        data[song]["votes"][str(author_id)] = vote
        self.save_data(data)


class ServerData:
    """Holds per-server configuration, state, and MongoDB collections."""

    def __init__(self, guild_id: str, config: dict, global_config: dict):
        self.guild_id = guild_id
        self.discord2name = global_config.get("discord2name", {}).get(guild_id, {})
        self.spotify2discord = config.get("spotifyIdToDiscordId", {})
        self.channel_id = config.get("spotifyChannelId")
        self.playlist_id = config.get("spotifyPlaylistId")
        self.new_playlist_id = config.get("spotifyNewPlaylistId")
        self.patch_message_url = config.get("spotifyRecapMessage")

        # Per-server MongoDB collections
        db = mongo_client[f"Playlist_{guild_id}"]
        self.playlist_items_full = db["playlistItemsFull"]
        self.votes_db = db["votes"]

        # Per-server state
        self.vote_infos = {}
        self.snapshot = {}
        self.reminders = {}

        # Per-server vote manager
        self.vote_manager = VoteManager(
            f"{DATA_FOLDER}/addwithvotes_{guild_id}.json", self.discord2name
        )


def _migrate_old_data(guild_id: str):
    """
    Auto-migrate old single-server data to the new per-server format.
    Runs once at startup; skips if the new data already exists.
    """
    import shutil

    new_db = mongo_client[f"Playlist_{guild_id}"]
    old_db = mongo_client["Playlist"]

    for col_name in ["playlistItemsFull", "votes"]:
        old_col = old_db[col_name]
        new_col = new_db[col_name]
        if new_col.count_documents({}) == 0 and old_col.count_documents({}) > 0:
            docs = list(old_col.find())
            new_col.insert_many(docs)
            logger.info(
                "[Migration] Copied %d documents from Playlist.%s to Playlist_%s.%s",
                len(docs), col_name, guild_id, col_name,
            )
        else:
            logger.debug("[Migration] Playlist_%s.%s already has data or source is empty, skipping.", guild_id, col_name)

    json_files = [
        ("addwithvotes.json", f"addwithvotes_{guild_id}.json"),
        ("voteinfos.json", f"voteinfos_{guild_id}.json"),
        ("snapshot.json", f"snapshot_{guild_id}.json"),
        ("reminderspotify.json", f"reminderspotify_{guild_id}.json"),
    ]
    for old_name, new_name in json_files:
        old_path = os.path.join(DATA_FOLDER, old_name)
        new_path = os.path.join(DATA_FOLDER, new_name)
        if os.path.exists(old_path) and not os.path.exists(new_path):
            shutil.copy2(old_path, new_path)
            logger.info("[Migration] Copied %s -> %s", old_name, new_name)
        else:
            logger.debug("[Migration] %s already exists or source missing, skipping.", new_name)


# Migrate old data for the primary (first) server before building ServerData
_migrate_old_data(str(ENABLED_SERVERS[0]))

# Build per-server data
SERVERS: dict[str, ServerData] = {}
for _guild_id in ENABLED_SERVERS:
    SERVERS[str(_guild_id)] = ServerData(
        str(_guild_id), MODULE_CONFIGS[_guild_id], CONFIG
    )


class Spotify(interactions.Extension):
    def __init__(self, bot: interactions.client):
        self.bot: interactions.Client = bot

    def get_server(self, guild_id) -> ServerData:
        """Get the server data for a given guild ID."""
        return SERVERS[str(guild_id)]

    @interactions.listen()
    async def on_startup(self):
        for server in SERVERS.values():
            await self.load_voteinfos(server)
            await self.load_snapshot(server)
            await self.load_reminders(server)
        self.check_playlist_changes.start()
        self.randomvote.start()
        self.reminder_check.start()
        self.check_for_end.start()
        self.new_titles_playlist.start()

    async def load_voteinfos(self, server: ServerData):
        try:
            with open(f"{DATA_FOLDER}/voteinfos_{server.guild_id}.json", "r", encoding="utf-8") as f:
                server.vote_infos.update(json.load(f))
        except FileNotFoundError:
            pass

    async def load_snapshot(self, server: ServerData):
        try:
            with open(f"{DATA_FOLDER}/snapshot_{server.guild_id}.json", "r", encoding="utf-8") as f:
                server.snapshot.update(json.load(f))
        except FileNotFoundError:
            server.snapshot = {"snapshot": "", "duration": 0, "length": 0}

    async def save_snapshot(self, server: ServerData):
        with open(f"{DATA_FOLDER}/snapshot_{server.guild_id}.json", "w", encoding="utf-8") as f:
            json.dump(server.snapshot, f, indent=4)

    async def save_voteinfos(self, server: ServerData):
        with open(f"{DATA_FOLDER}/voteinfos_{server.guild_id}.json", "w", encoding="utf-8") as f:
            json.dump(server.vote_infos, f, indent=4)

    async def load_reminders(self, server: ServerData):
        try:
            with open(
                f"{DATA_FOLDER}/reminderspotify_{server.guild_id}.json", "r", encoding="utf-8"
            ) as file:
                reminders_data = json.load(file)
                for remind_time_str, user_ids in reminders_data.items():
                    remind_time = datetime.strptime(
                        remind_time_str, "%Y-%m-%d %H:%M:%S"
                    )
                    server.reminders[remind_time] = set(user_ids)
        except FileNotFoundError:
            pass

    async def save_reminders(self, server: ServerData):
        reminders_data = {
            remind_time.strftime("%Y-%m-%d %H:%M:%S"): list(user_ids)
            for remind_time, user_ids in server.reminders.items()
        }
        with open(f"{DATA_FOLDER}/reminderspotify_{server.guild_id}.json", "w", encoding="utf-8") as file:
            json.dump(reminders_data, file, indent=4)

    @interactions.slash_command(
        "addsong",
        description="Ajoute une chanson à la playlist.",
        scopes=ENABLED_SERVERS,
    )
    @interactions.slash_option(
        name="song",
        description="Nom de la chanson à ajouter",
        opt_type=interactions.OptionType.STRING,
        required=True,
        autocomplete=True,
    )
    async def addsong(self, ctx: interactions.SlashContext, song):
        server = self.get_server(ctx.guild_id)
        if str(ctx.channel_id) != str(server.channel_id):
            await ctx.send(
                "Vous ne pouvez pas utiliser cette commande dans ce salon.",
                ephemeral=True,
            )
            logger.info(
                "Commande /addsong utilisée dans un mauvais salon(%s)", ctx.channel.name
            )
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
            await ctx.send("Cette chanson n'existe pas.", ephemeral=True)
            logger.info("Commande /addsong utilisée avec une chanson inexistante")
            return

        if song_data["_id"] not in server.playlist_items_full.distinct("_id"):
            server.playlist_items_full.insert_one(song_data)
            sp.playlist_add_items(server.playlist_id, [song_data["_id"]])
            embed, file = await embed_song(
                song=song_data,
                track=track,
                embedtype=EmbedType.ADD,
                time=interactions.Timestamp.utcnow(),
                person=ctx.author.username,
                icon=ctx.author.avatar.url,
            )
            await ctx.send(
                content=f"{random.choice(startList)} {ctx.author.mention}, {random.choice(finishList)}",
                embeds=embed,
                files=[file] if file else None,
            )
            logger.info("%s ajouté par %s", track["name"], ctx.author.display_name)
        else:
            await ctx.send(
                "Cette chanson a déjà été ajoutée à la playlist.", ephemeral=True
            )
            logger.info("Commande /addsong utilisée avec une chanson déjà présente")

    @addsong.autocomplete("song")
    async def autocomplete_from_spotify(self, ctx: interactions.AutocompleteContext):
        if not ctx.input_text:
            choices = [{"name": "Veuillez entrer un nom de chanson", "value": "error"}]
        else:
            items = sp.search(ctx.input_text, limit=10, type="track", market="FR")[
                "tracks"
            ]["items"]
            if not items:
                choices = [{"name": "Aucun résultat", "value": "error"}]
            else:
                choices = [
                    {
                        "name": f"{item['artists'][0]['name']} - {item['name']} (Album: {item['album']['name']})"[
                            :100
                        ],
                        "value": item["uri"],
                    }
                    for item in items
                ]
        await ctx.send(choices=choices)

    # @interactions.Task.create(
    #     interactions.OrTrigger(
    #         interactions.TimeTrigger(hour=20, minute=0, utc=False),
    #         interactions.TimeTrigger(hour=21, minute=30, utc=False),
    #     )
    # )
    @interactions.Task.create(interactions.TimeTrigger(hour=20, minute=0, utc=False))
    async def randomvote(self):
        for server in SERVERS.values():
            try:
                await self._randomvote_for_server(server)
            except Exception as e:
                logger.error("Error in randomvote for server %s: %s", server.guild_id, e)

    async def _randomvote_for_server(self, server: ServerData):
        logger.info("Tache randomvote lancée pour le serveur %s", server.guild_id)
        message_id = server.vote_infos.get("message_id")
        track_id = server.vote_infos.get("track_id")
        if not message_id or not track_id:
            logger.warning(
                "Pas de vote en cours pour le serveur %s, lancement d'un nouveau vote",
                server.guild_id,
            )
            await self._start_new_vote(server)
            return
        logger.debug("message_id: %s", message_id)
        logger.debug("track_id: %s", track_id)
        channel = self.bot.get_channel(server.channel_id)
        message = await channel.fetch_message(message_id)
        logger.debug("message : %s", str(message.id))
        votes = server.votes_db.find_one({"_id": track_id})
        song = server.playlist_items_full.find_one({"_id": track_id})
        logger.debug("song : %s\ntrack_id : %s", song, track_id)
        track = sp.track(track_id, market="FR")
        conserver, supprimer, menfou, users = count_votes(votes["votes"], server.discord2name)

        total_votes = conserver + supprimer + menfou
        if total_votes < 3:
            new_time = str(self.randomvote.next_run)
            embed_original = message.embeds[0]
            embed_original.title = f"Vote prolongé jusqu'à {interactions.utils.timestamp_converter(new_time).format(interactions.TimestampStyles.RelativeTime)}"
            embed_original.timestamp = new_time
            await message.edit(
                content=f"Pas assez de votes ({total_votes}/3), le vote est prolongé de 24h !\nVoulez-vous **conserver** cette chanson dans playlist ? (poke <@{song['added_by']}>)",
                embeds=[embed_original],
            )
            logger.info(f"Vote prolongé de 24h car seulement {total_votes} votes")
            return

        logger.debug(
            "keep : %s\nremove : %s\nmenfou : %s",
            str(conserver),
            str(supprimer),
            str(menfou),
        )

        await message.unpin()
        if supprimer > conserver or (conserver == 0 and supprimer == 0 and menfou >= 3):
            embed, file = await embed_song(
                song=song,
                track=track,
                embedtype=EmbedType.VOTE_LOSE,
                time=interactions.Timestamp.now(),
            )
            await message.edit(
                content="La chanson a été supprimée.",
                embeds=[
                    embed,
                    await embed_message_vote(
                        keep=conserver,
                        remove=supprimer,
                        menfou=menfou,
                        users=users,
                        color=interactions.MaterialColors.DEEP_ORANGE,
                    ),
                ],
                components=[],
            )
            sp.playlist_remove_all_occurrences_of_items(server.playlist_id, [track_id])
            server.playlist_items_full.delete_one({"_id": track_id})
            server.votes_db.find_one_and_update(
                {"_id": track_id}, {"$set": {"state": "supprimée"}}
            )
            logger.info("La chanson a été supprimée.")
            await self._check_playlist_changes_for_server(server)
        else:
            logger.debug("La chanson a été conservée.")
            logger.debug("track_id : %s\nmessage_id : %s", track_id, message_id)
            embed, file = await embed_song(
                song=song,
                track=track,
                embedtype=EmbedType.VOTE_WIN,
                time=interactions.Timestamp.now(),
            )
            await message.edit(
                content="La chanson a été conservée.",
                embeds=[
                    embed,
                    await embed_message_vote(
                        keep=conserver,
                        remove=supprimer,
                        menfou=menfou,
                        users=users,
                        color=interactions.MaterialColors.LIME,
                    ),
                ],
                components=[],
            )
            server.votes_db.find_one_and_update(
                {"_id": track_id}, {"$set": {"state": "conservée"}}
            )
            logger.info("La chanson a été conservée.")
        await self._start_new_vote(server)

    async def _start_new_vote(self, server: ServerData):
        """Pick a random song and start a new vote for a server."""
        track_ids = set(server.playlist_items_full.distinct("_id"))
        pollhistory = set(server.votes_db.distinct("_id"))
        track_id = random.choice(list(track_ids))
        logger.debug("track_id choisie : %s", track_id)
        while track_id in pollhistory:
            logger.warning(
                "Chanson déjà votée, nouvelle chanson tirée au sort (%s)", track_id
            )
            track_id = random.choice(list(track_ids))
        logger.info("Chanson tirée au sort : %s", track_id)
        song = server.playlist_items_full.find_one({"_id": track_id})
        track = sp.track(song["_id"], market="FR")
        channel = await self.bot.fetch_channel(server.channel_id)
        embed, file = await embed_song(
            song=song,
            track=track,
            embedtype=EmbedType.VOTE,
            time=str(self.randomvote.next_run),
        )
        message = await channel.send(
            content=f"Voulez-vous **conserver** cette chanson dans playlist ? (poke <@{song['added_by']}>)",
            embeds=[
                embed,
                # await embed_message_vote(),
            ],
            components=[
                interactions.ActionRow(
                    interactions.Button(
                        label="Conserver",
                        style=interactions.ButtonStyle.SUCCESS,
                        emoji="✅",
                        custom_id="conserver",
                    ),
                    interactions.Button(
                        label="Supprimer",
                        style=interactions.ButtonStyle.DANGER,
                        emoji="🗑️",
                        custom_id="supprimer",
                    ),
                    interactions.Button(
                        label="Menfou",
                        style=interactions.ButtonStyle.SECONDARY,
                        emoji="🤷",
                        custom_id="menfou",
                    ),
                    interactions.Button(
                        label="Annuler",
                        style=interactions.ButtonStyle.SECONDARY,
                        emoji="❌",
                        custom_id="annuler",
                    ),
                ),
            ],
            files=[file] if file else None,
        )
        await message.pin()
        await channel.purge(deletion_limit=1, after=message)
        server.vote_infos.update({"message_id": str(message.id), "track_id": track_id})
        await self.save_voteinfos(server)
        server.votes_db.update_one(
            {"_id": track_id},
            {
                "$set": {
                    "name": f"{', '.join(artist['name'] for artist in track['artists'])} - {track['name']}",
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "added_by": song["added_by"],
                    "votes": {},
                }
            },
            upsert=True,
        )

    @interactions.listen(Component)
    async def on_component(self, event: Component):
        """Called when a component is clicked"""
        ctx = event.ctx
        logger.debug("ctx.custom_id : %s", ctx.custom_id)
        if ctx.custom_id not in ["conserver", "supprimer", "menfou", "annuler"]:
            return
        server = self.get_server(ctx.guild_id)
        # Check if the user has voted recently
        user_id = str(ctx.user.id)
        if user_id in last_votes and time.time() - last_votes[user_id] < COOLDOWN_TIME:
            await ctx.send(
                "Tu ne peux voter que toutes les 5 secondes ⚠️", ephemeral=True
            )
            logger.warning("%s a essayé de voter trop rapidement", ctx.user.username)
            return
        last_votes[user_id] = time.time()
        message_id = server.vote_infos.get("message_id")
        track_id = server.vote_infos.get("track_id")
        if ctx.message.id == int(message_id):
            embed_original = ctx.message.embeds[0]
            # Check if the user has already voted and update their vote if necessary
            user_id = str(ctx.user.id)
            if ctx.custom_id == "annuler":
                votes = server.votes_db.find_one_and_update(
                    {"_id": track_id},
                    {"$unset": {f"votes.{user_id}": ""}},
                    return_document=pymongo.ReturnDocument.AFTER,
                )
            else:
                votes = server.votes_db.find_one_and_update(
                    {"_id": track_id},
                    {"$set": {f"votes.{user_id}": ctx.custom_id}},
                    upsert=True,
                    return_document=pymongo.ReturnDocument.AFTER,
                )
            logger.info("User %s voted %s", ctx.user.username, ctx.custom_id)
            # Count the votes
            conserver, supprimer, menfou, users = count_votes(
                votes["votes"], server.discord2name
            )
            users = ", ".join(users)
            logger.info(
                "Votes : %s conserver, %s supprimer, %s menfou",
                conserver,
                supprimer,
                menfou,
            )
            embed_original.fields[4].value = (
                f"{conserver+supprimer+menfou} vote{'s' if conserver+supprimer+menfou>1 else ''} ({users})"
            )
            # await ctx.message.edit(content=f"Voulez-vous conserver cette chanson dans playlist ?")
            # Update the message with the vote counts

            await ctx.message.edit(
                embeds=[
                    embed_original,
                    # await embed_message_vote(keep, remove, menfou),
                ],
            )

            # Send a message to the user informing them that their vote has been counted
            if ctx.custom_id == "annuler":
                await ctx.send(
                    "Ton vote a bien été annulé ! 🗳️",
                    ephemeral=True,
                )
            else:
                await ctx.send(
                    f"Ton vote pour **{ctx.custom_id}** cette musique a bien été pris en compte ! 🗳️",
                    ephemeral=True,
                )

    @interactions.Task.create(interactions.IntervalTrigger(minutes=1, seconds=0))
    async def check_playlist_changes(self):
        """
        Check for changes in the Spotify playlist and update the Discord message accordingly.
        """
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

        # Retrieve the channel where messages will be sent
        channel = await self.bot.fetch_channel(server.channel_id)
        logger.debug(
            "old_snap : %s, duration : %s, length : %s",
            server.snapshot.get("snapshot"),
            server.snapshot.get("duration"),
            server.snapshot.get("length"),
        )
        # Compare the current snapshot ID to the previous snapshot ID
        try:
            new_snap = sp.playlist(server.playlist_id, fields="snapshot_id")["snapshot_id"]
        except spotipy.SpotifyException as e:
            logger.error("Spotify API Error : %s", e)
            return
        except ConnectionError as e:
            logger.error("ConnectionError : %s", e)
            return

        if new_snap != server.snapshot.get("snapshot"):
            # Retrieve the tracks of the playlist
            try:
                results = sp.playlist_tracks(
                    playlist_id=server.playlist_id, limit=100, offset=0
                )
            except spotipy.SpotifyException as e:
                logger.error("Spotify API Error : %s", e)
                return
            tracks = results["items"]
            # get next 100 tracks
            while results["next"]:
                results = sp.next(results)
                tracks.extend(results["items"])
            # Process each track
            length = len(tracks)
            duration = 0
            # Compare the current track IDs to the previous track IDs
            last_track_ids = server.playlist_items_full.distinct("_id")
            current_track_ids = {track["track"]["id"] for track in tracks}
            added_track_ids = list(set(current_track_ids) - set(last_track_ids))
            removed_track_ids = list(set(last_track_ids) - set(current_track_ids))
            logger.debug("added_track_ids : %s", added_track_ids)
            logger.debug("removed_track_ids : %s", removed_track_ids)
            for track in tracks:
                # Append the track to a list of tracks to be inserted into the MongoDB collection
                song = spotifymongoformat(track, spotify2discord=server.spotify2discord)
                # Retrieve the time the track was added and add its duration to the total duration of the playlist
                duration += track["track"]["duration_ms"]
                # Send messages for added or removed tracks
                if track["track"]["id"] in added_track_ids:
                    song = spotifymongoformat(track, spotify2discord=server.spotify2discord)
                    track = sp.track(track["track"]["id"], market="FR")
                    server.playlist_items_full.insert_one(song)
                    dt = interactions.utils.timestamp_converter(
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
                        content=f"{random.choice(startList)} <@{song['added_by']}>, {random.choice(finishList)}\n{track['external_urls']['spotify']}",
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
                    song = server.playlist_items_full.find_one_and_delete({"_id": track_id})
                    track = sp.track(track_id, market="FR")
                    embed, file = await embed_song(
                        song=song,
                        track=track,
                        embedtype=EmbedType.DELETE,
                        time=interactions.Timestamp.utcnow(),
                    )
                    channel = await self.bot.fetch_channel(server.channel_id)
                    await channel.send(
                        track["external_urls"]["spotify"],
                        embeds=embed,
                        files=[file] if file else None,
                    )

            # Store the snapshot ID, length and duration in a JSON file
            server.snapshot["snapshot"] = new_snap
            server.snapshot["length"] = length
            server.snapshot["duration"] = duration
            await self.save_snapshot(server)
            logger.debug("Snapshot mis à jour")
            # Send a message indicating that the playlist has been updated
        message = f"Dernière màj de la playlist {interactions.Timestamp.utcnow().format(interactions.TimestampStyles.RelativeTime)}, si c'était il y a plus d'**une minute**, il y a probablement un problème\n`/addsong Titre et artiste de la chanson` pour ajouter une chanson\nIl y a actuellement **{server.snapshot.get('length', 0)}** chansons dans la playlist, pour un total de **{milliseconds_to_string(server.snapshot.get('duration', 0))}**\nDashboard : https://drndvs.link/StatsPlaylist"
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=5)
            ) as session:
                async with session.patch(
                    url=server.patch_message_url,
                    json={
                        "content": message,
                    },
                ) as response:
                    response.raise_for_status()
        except aiohttp.ClientError as e:
            logger.error("Error while trying to patch message : %s", e)
        except TimeoutError:
            logger.error("TimeoutError while trying to patch message")

    @interactions.slash_command(
        name="rappelvote",
        sub_cmd_name="set",
        description="Gère les rappels pour voter",
        sub_cmd_description="Ajoute un rappel pour voter pour la chanson du jour",
        scopes=ENABLED_SERVERS,
    )
    @interactions.slash_option(
        name="heure",
        description="Heure du rappel",
        opt_type=interactions.OptionType.INTEGER,
        required=True,
        min_value=0,
        max_value=23,
    )
    @interactions.slash_option(
        "minute",
        "Minute du rappel",
        interactions.OptionType.INTEGER,
        required=True,
        min_value=0,
        max_value=59,
    )
    async def setreminder(self, ctx: interactions.SlashContext, heure, minute):
        """
        Sets a reminder for a user to vote at a specific time.

        Args:
            ctx (interactions.SlashContext): The context of the slash command.
            heure (int): The hour of the reminder.
            minute (int): The minute of the reminder.
        """
        server = self.get_server(ctx.guild_id)
        if str(ctx.channel_id) == str(server.channel_id):
            logger.info(
                "%s a ajouté un rappel à %s:%s", ctx.user.display_name, heure, minute
            )
            remind_time = datetime.strptime(f"{heure}:{minute}", "%H:%M")
            current_time = datetime.now()
            remind_time = current_time.replace(
                hour=remind_time.hour,
                minute=remind_time.minute,
                second=0,
                microsecond=0,
            )
            if remind_time <= current_time:
                remind_time += timedelta(days=1)
            if remind_time not in server.reminders:
                server.reminders[remind_time] = set()
            server.reminders[remind_time].add(ctx.user.id)
            await self.save_reminders(server)

            await ctx.send(
                f"Rappel défini à {remind_time.strftime('%H:%M')}.", ephemeral=True
            )
        else:
            await ctx.send(
                "Cette commande n'est pas disponible dans ce salon.", ephemeral=True
            )
            logger.info(
                "%s a essayé d'utiliser la commande /rappel dans le salon #%s (%s)",
                ctx.user.display_name,
                ctx.channel.name,
                ctx.channel_id,
            )

    @interactions.Task.create(interactions.IntervalTrigger(minutes=1))
    async def reminder_check(self):
        for server in SERVERS.values():
            try:
                await self._reminder_check_for_server(server)
            except Exception as e:
                logger.error(
                    "Error in reminder_check for server %s: %s", server.guild_id, e
                )

    async def _reminder_check_for_server(self, server: ServerData):
        logger.debug("reminder_check lancé pour le serveur %s", server.guild_id)
        current_time = datetime.now()
        reminders_to_remove = []
        for remind_time, user_ids in server.reminders.copy().items():
            if current_time >= remind_time:
                for user_id in user_ids.copy():
                    user = await self.bot.fetch_user(user_id)
                    if user:
                        vote = server.votes_db.find_one(
                            {"_id": str(server.vote_infos["track_id"])}
                        )["votes"].get(str(user_id))
                        if vote is None:
                            await user.send(
                                f"Hey {user.mention}, tu n'as pas voté aujourd'hui :pleading_face: \nhttps://discord.com/channels/{server.guild_id}/{server.channel_id}/{server.vote_infos.get('message_id')}"
                            )
                            logger.debug("Rappel envoyé à %s", user.display_name)
                        else:
                            logger.debug(
                                "%s a déjà voté aujourd'hui !, pas de rappel envoyé",
                                user.display_name,
                            )
                    next_remind_time = remind_time + timedelta(days=1)
                    if next_remind_time not in server.reminders:
                        server.reminders[next_remind_time] = set()
                    server.reminders[next_remind_time].add(user_id)
                    user_ids.remove(user_id)
                if not user_ids:
                    reminders_to_remove.append(remind_time)
        for remind_time in reminders_to_remove:
            del server.reminders[remind_time]

        await self.save_reminders(server)

    @setreminder.subcommand(
        sub_cmd_name="remove",
        sub_cmd_description="Enlève un rappel de vote pour la chanson du jour",
    )
    async def deletereminder(self, ctx: interactions.SlashContext):
        server = self.get_server(ctx.guild_id)
        user_id = ctx.user.id
        # create the list of reminders for the user
        reminders_list = []
        for remind_time, user_ids in server.reminders.copy().items():
            if user_id in user_ids:
                reminders_list.append(remind_time)
        # Create a button for each reminder
        buttons = [
            interactions.Button(
                label=remind_time.strftime("%H:%M"),
                style=interactions.ButtonStyle.SECONDARY,
                custom_id=str(remind_time.timestamp()),
            )
            for remind_time in reminders_list
        ]
        # Send a message with the buttons
        await ctx.send(
            "Quel rappel veux-tu supprimer ?",
            components=[interactions.ActionRow(*buttons)],
            ephemeral=True,
        )
        try:
            # Wait for the user to click a button
            button_ctx: Component = await self.bot.wait_for_component(
                components=[
                    str(remind_time.timestamp()) for remind_time in reminders_list
                ],
                timeout=60,
            )
            # Remove the reminder from the reminders dictionary
            remind_time = datetime.fromtimestamp(float(button_ctx.ctx.custom_id))
            server.reminders[remind_time].remove(user_id)
            if not server.reminders[remind_time]:
                del server.reminders[remind_time]
            # Save the reminders to a JSON file
            await self.save_reminders(server)
            # Send a message to the user indicating that the reminder has been removed
            await button_ctx.ctx.edit_origin(
                content=f"Rappel à {remind_time.strftime('%H:%M')} supprimé.",
                components=[],
            )
            logger.info(
                "Rappel à %s supprimé pour %s",
                remind_time.strftime("%H:%M"),
                ctx.user.display_name,
            )
        except TimeoutError:
            await ctx.send(
                "Tu n'as pas sélectionné de rappel à supprimer.", ephemeral=True
            )
            await button_ctx.ctx.edit_origin(
                content="Aucun rappel sélectionné.", components=[]
            )

    @interactions.slash_command(
        name="updatetoken",
        description="Met à jour le token de l'application Spotify",
        scopes=[DEV_GUILD],
    )
    async def updatetoken(self, ctx: interactions.SlashContext):
        # Create a SpotifyOAuth object to handle authentication
        sp_oauth = spotipy.SpotifyOAuth(
            client_id=SPOTIFY_CLIENT_ID,
            redirect_uri=SPOTIFY_REDIRECT_URI,
            client_secret=SPOTIFY_CLIENT_SECRET,
            scope="playlist-modify-private playlist-read-private playlist-modify-public playlist-read-collaborative",
            open_browser=False,
            cache_handler=spotipy.CacheFileHandler("data/.cache"),
        )

        # Check if a valid token is already cached
        token_info = sp_oauth.get_cached_token()
        if token_info:
            logger.info(
                "token_info : %s\nIsExpired : %s\nIsValid : %s",
                token_info,
                sp_oauth.is_token_expired(token_info),
                sp_oauth.validate_token(token_info),
            )
        # If the token is invalid or doesn't exist, prompt the user to authenticate
        # Generate the authorization URL and prompt the user to visit it
        auth_url = sp_oauth.get_authorize_url()
        modal = interactions.Modal(
            interactions.ShortText(
                label="Auth URL :", value=auth_url, custom_id="auth_url"
            ),
            interactions.ParagraphText(label="Answer URL :", custom_id="answer_url"),
            title="Spotify Auth",
        )
        await ctx.send_modal(modal)
        modal_ctx: interactions.ModalContext = await ctx.bot.wait_for_modal(modal)
        # Wait for the user to input the response URL after authenticating
        auth_code = modal_ctx.responses["answer_url"]
        # Exchange the authorization code for an access token and refresh token
        token_info = sp_oauth.get_access_token(
            sp_oauth.parse_response_code(auth_code), as_dict=False
        )
        await modal_ctx.send("Token mis à jour !", ephemeral=True)
        # Create a new instance of the Spotify API with the access token
        sp = spotipy.Spotify(auth_manager=sp_oauth, language="fr")

    @interactions.slash_command(
        name="songinfo",
        description="Affiche les informations d'une chanson",
        scopes=ENABLED_SERVERS,
    )
    @interactions.slash_option(
        name="song",
        description="Nom de la chanson",
        opt_type=interactions.OptionType.STRING,
        required=True,
        autocomplete=True,
        argument_name="song_id",
    )
    async def songinfo(self, ctx: interactions.SlashContext, song_id):
        """
        Displays information about a song from the mongodb database.
        """
        server = self.get_server(ctx.guild_id)
        embed = None
        song = server.playlist_items_full.find_one({"_id": song_id})
        votes = server.votes_db.find_one({"_id": song_id})
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
                time=interactions.Timestamp.utcnow(),
                person=votes.get("added_by", "Inconnu"),
            )
        if votes:
            if votes.get("votes"):
                conserver, supprimer, menfou, users = count_votes(
                    votes.get("votes", {}), server.discord2name
                )
                # Create a Timestamp object from the date string and a None object if the date is not present
                date = votes.get("date")
                if date:
                    date = interactions.utils.timestamp_converter(
                        datetime.strptime(date, "%Y-%m-%d")
                    ).format(interactions.TimestampStyles.LongDate)
                embeds = [
                    embed,
                    await embed_message_vote(
                        keep=conserver,
                        remove=supprimer,
                        menfou=menfou,
                        users=users,
                        color=0x1DB954,
                        description=f"Vote effectué le {date}\nLa chanson a été **{votes.get('state', '')}**",
                    ),
                ]
            else:
                embeds = [
                    embed,
                    interactions.Embed(
                        title="Vote",
                        description=f"La chanson est passée au vote et a été **{votes.get('state', '')}**\nPas de détails sur le vote.",
                        color=0x1DB954,
                    ),
                ]
        else:
            embeds = [embed]
        await ctx.send(embeds=embeds, files=[file] if file else None)
        if not song and not votes:
            await ctx.send("Cette chanson n'existe pas.", ephemeral=True)

    @songinfo.autocomplete("song")
    async def autocomplete_from_db(self, ctx: interactions.AutocompleteContext):
        """
        Autocomplete function for the 'songinfo' command.
        """
        server = self.get_server(ctx.guild_id)
        if not ctx.input_text:
            choices = [
                {
                    "name": "Veuillez entrer un nom de chanson",
                    "value": "error",
                }
            ]
        else:
            # Search for tracks in the name and artists array fields of the MongoDB collection
            words = ctx.input_text.split()

            # Create a single regex pattern that matches any of the words
            regex_pattern = "|".join(words)

            query = {
                "$or": [
                    {"name": {"$regex": regex_pattern, "$options": "i"}},
                    {"artists": {"$regex": regex_pattern, "$options": "i"}},
                ]
            }
            # Fetch data from playlist_items_full and votes_db
            playlist_items = {
                item["_id"]: item for item in server.playlist_items_full.find(query)
            }
            votes = {item["_id"]: item for item in server.votes_db.find(query)}

            # Merge dictionaries. In case of conflict, keep the entry from playlist_items_full
            results = {**playlist_items, **votes}
            if not results:
                choices = [
                    {
                        "name": "Aucun résultat",
                        "value": "error",
                    }
                ]
            else:
                # Format search results for autocomplete choices
                choices = [
                    {
                        "name": (
                            f"{', '.join(result['artists'])} - {result['name']}"
                            if result.get("artists")
                            else f"{result['name']}"
                        )[
                            :100
                        ],  # limit the entire string to 100 characters
                        "value": result["_id"],
                    }
                    for songresult_id, result in results.items()
                ]
                logger.debug("choices : %s", choices)
        await ctx.send(choices=choices[0:25])

    @interactions.slash_command(
        name="addwithvote",
        description="Si vous êtes pas sûr d'ajoouter une chanson, vous pouvez la mettre au vote",
        scopes=ENABLED_SERVERS,
    )
    @interactions.slash_option(
        name="song",
        description="Nom de la chanson",
        opt_type=interactions.OptionType.STRING,
        required=True,
        autocomplete=True,
    )
    async def addwithvote(self, ctx: interactions.SlashContext, song):
        server = self.get_server(ctx.guild_id)
        if str(ctx.channel_id) == str(server.channel_id):
            # Get last track IDs from MongoDB
            last_track_ids = server.playlist_items_full.distinct("_id")
            logger.info(
                "/addwithvote '%s' utilisé par %s(id:%s)",
                song,
                ctx.author.username,
                ctx.author_id,
            )
            try:
                # Get track info from Spotify API
                track = sp.track(song, market="FR")
                song = spotifymongoformat(
                    track, ctx.author_id, spotify2discord=server.spotify2discord
                )
            except spotipy.exceptions.SpotifyException:
                await ctx.send("Cette chanson n'existe pas.", ephemeral=True)
                logger.info("Commande /addsong utilisée avec une chanson inexistante")
            data = server.vote_manager.load_data()
            # List all song_id in data
            song_ids = list(data.keys())
            if song["_id"] not in last_track_ids and song["_id"] not in song_ids:
                logger.debug("song : %s", song)
                # Create and send embed message
                components = [
                    interactions.ActionRow(
                        interactions.Button(
                            label="Oui",
                            style=interactions.ButtonStyle.SUCCESS,
                            emoji="✅",
                            custom_id=f"addwithvote_{song['_id']}_yes",
                        ),
                        interactions.Button(
                            label="Non",
                            style=interactions.ButtonStyle.DANGER,
                            emoji="🗑️",
                            custom_id=f"addwithvote_{song['_id']}_no",
                        ),
                        interactions.Button(
                            label="Annuler",
                            style=interactions.ButtonStyle.SECONDARY,
                            emoji="❌",
                            custom_id=f"addwithvote_{song['_id']}_annuler",
                        ),
                    ),
                ]
                time = (datetime.now() + timedelta(days=1)).replace(
                    minute=0, second=0, microsecond=0
                )
                if time < datetime.now() + timedelta(days=1):
                    time += timedelta(hours=1)
                embed, file = await embed_song(
                    song=song,
                    track=track,
                    embedtype=EmbedType.VOTE_ADD,
                    time=time,
                    person=ctx.author.id,
                    icon=ctx.author.avatar.url,
                )
                message = await ctx.send(
                    content=f"Voulez-vous **ajouter** cette chanson à la playlist ? (Demandé par <@{ctx.author_id}>)\n{track['external_urls']['spotify']}",
                    embeds=embed,
                    files=[file] if file else None,
                    components=components,
                )
                # Append the song, message ID and track ID to the votewithadd dictionary
                data = server.vote_manager.load_data()
                data[song["_id"]] = {
                    "channel_id": ctx.channel.id,
                    "message_id": message.id,
                    "author_id": ctx.author.id,
                    "deadline": time.timestamp(),
                    "votes": {
                        str(ctx.author.id): "yes",
                    },
                }
                server.vote_manager.save_data(data)
                logger.info(
                    "%s ajouté au vote par %s", track["name"], ctx.author.display_name
                )
            else:
                await ctx.send(
                    "Cette chanson est déjà dans la playlist", ephemeral=True
                )
                logger.info(
                    "Commande /addwithvote utilisée avec une chanson déjà présente"
                )
        else:
            await ctx.send(
                "Vous ne pouvez pas utiliser cette commande dans ce salon.",
                ephemeral=True,
            )
            logger.info(
                "Commande /addwithvote utilisée dans un mauvais salon(%s)",
                ctx.channel.name,
            )

    @interactions.listen(Component)
    async def on_button2(self, event: Component):
        if not event.ctx.custom_id.startswith("addwithvote"):
            return
        server = self.get_server(event.ctx.guild_id)
        # extract the song_id and the vote from the custom_id
        song_id = event.ctx.custom_id.split("_")[1]
        vote = event.ctx.custom_id.split("_")[2]
        # check if the user has voted recently
        user_id = str(event.ctx.user.id)
        if user_id in last_votes and time.time() - last_votes[user_id] < COOLDOWN_TIME:
            await event.ctx.send(
                "Tu ne peux voter que toutes les 5 secondes ⚠️", ephemeral=True
            )
            logger.warning(
                "%s a essayé de voter trop rapidement", event.ctx.user.username
            )
            return
        last_votes[user_id] = time.time()
        # check if the user has already voted and update their vote if necessary
        data = server.vote_manager.load_data()
        if vote == "annuler":
            data[song_id]["votes"].pop(user_id, None)
            server.vote_manager.save_data(data)
        else:
            server.vote_manager.save_vote(user_id, vote, song_id)
        # count the votes
        data = server.vote_manager.load_data()
        yes, no, users = server.vote_manager.count_votes(data, song_id)
        # update the message with the vote counts
        users = ", ".join(users)
        embed_original = event.ctx.message.embeds[0]
        embed_original.fields[4].value = (
            f"{yes+no} vote{'s' if yes+no>1 else ''} ({users})"
        )
        await event.ctx.message.edit(embeds=[embed_original])
        # send a message to the user informing them that their vote has been counted
        if vote == "annuler":
            await event.ctx.send(
                "Ton vote a bien été annulé ! 🗳️",
                ephemeral=True,
            )
        else:
            await event.ctx.send(
                f"Ton vote pour **{vote}** cette musique a bien été pris en compte ! 🗳️",
                ephemeral=True,
            )
        logger.info("User %s voted %s", event.ctx.user.username, vote)

    @addwithvote.autocomplete("song")
    async def autocomplete_from_spotify(self, ctx: interactions.AutocompleteContext):
        """
        Autocomplete function for the 'addwithvote' command.
        """
        if not ctx.input_text:
            choices = [
                {
                    "name": "Veuillez entrer un nom de chanson",
                    "value": "error",
                }
            ]
        else:
            # Search for tracks on Spotify
            items = sp.search(ctx.input_text, limit=10, type="track", market="FR")[
                "tracks"
            ]["items"]
            if not items:
                choices = [
                    {
                        "name": "Aucun résultat",
                        "value": "error",
                    }
                ]
            else:
                # Format search results for autocomplete choices
                choices = [
                    {
                        "name": f"{item['artists'][0]['name']} - {item['name']} (Album: {item['album']['name']})"[
                            :100
                        ],
                        "value": item["uri"],
                    }
                    for item in items
                ]
        await ctx.send(choices=choices)

    async def endvote(self, song_id: str, server: ServerData):
        """
        End the vote for a given song.

        Args:
            song_id (str): The song ID to end the vote for.
            server (ServerData): The server data.
        """
        data = server.vote_manager.load_data()
        yes_votes, no_votes, users = server.vote_manager.count_votes(data, song_id)
        # Get the message
        channel = await self.bot.fetch_channel(data[song_id]["channel_id"])
        message = await channel.fetch_message(data[song_id]["message_id"])
        try:
            # Get track info from Spotify API
            track = sp.track(song_id, market="FR")
            song = spotifymongoformat(
                track, data[song_id]["author_id"], spotify2discord=server.spotify2discord
            )
        except spotipy.exceptions.SpotifyException as e:
            logger.error("Spotify API Error while using /addwithvote: %s", e)
        if yes_votes > no_votes:
            # Add song to MongoDB and Spotify playlist
            logger.debug("song : %s", song)
            server.playlist_items_full.insert_one(song)
            sp.playlist_add_items(server.playlist_id, [song["_id"]])
            embed, file = await embed_song(
                song=song,
                track=track,
                embedtype=EmbedType.VOTE_WIN,
                time=interactions.Timestamp.utcnow(),
                person=data[song_id]["author_id"],
            )
            await message.edit(
                content="La chanson a été ajoutée à la playlist.",
                embeds=[
                    embed,
                    await embed_message_vote_add(yes_votes, no_votes, users),
                ],
                components=[],
            )
            logger.info("La chanson a été ajoutée à la playlist.")
        else:
            embed, file = await embed_song(
                song=song,
                track=track,
                embedtype=EmbedType.VOTE_LOSE,
                time=interactions.Timestamp.utcnow(),
                person=data[song_id]["author_id"],
            )
            await message.edit(
                content="La chanson n'a pas été ajoutée à la playlist.",
                embeds=[
                    embed,
                    await embed_message_vote_add(yes_votes, no_votes, users),
                ],
                components=[],
            )
            logger.info("La chanson n'a pas été ajoutée à la playlist.")
        # Remove the vote from the data dictionary
        data.pop(song_id)
        server.vote_manager.save_data(data)

    @interactions.Task.create(
        interactions.OrTrigger(
            *[interactions.TimeTrigger(hour=hour) for hour in range(24)]
        )
    )
    async def check_for_end(self):
        """
        Check if the vote has ended for each song and end it if necessary.
        """
        for server in SERVERS.values():
            try:
                data = server.vote_manager.load_data()
                songs_to_end = []
                for song_id in data:
                    if server.vote_manager.check_deadline(song_id):
                        await self.endvote(song_id, server)
                        songs_to_end.append(song_id)
                for song_id in songs_to_end:
                    data.pop(song_id)
                server.vote_manager.save_data(data)
            except Exception as e:
                logger.error(
                    "Error in check_for_end for server %s: %s", server.guild_id, e
                )

    @interactions.Task.create(interactions.TimeTrigger(hour=4, minute=30, utc=False))
    async def new_titles_playlist(self):
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

    @interactions.slash_command(
        name="nextvote",
        description="Force le prochain vote PAS TOUCHE",
        scopes=[DEV_GUILD],
    )
    async def nextvote(self, ctx: interactions.SlashContext):
        """
        Force the next vote for the song of the day.
        """
        await self.randomvote()
        await ctx.send("Vote forcé", ephemeral=True)
