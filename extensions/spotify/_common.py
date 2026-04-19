"""Shared state, constants, data classes, and embed builders for the Spotify extension.

Kept separate from ``__init__.py`` so submodules (``auth``, ``playlist``,
``votes``) can import from here without triggering an import cycle through the
package root.
"""

import io
import os
import re
from datetime import datetime
from enum import Enum
from typing import Any

import aiohttp
import interactions
from interactions import Message

from src import logutil
from src.config_manager import load_config, load_discord2name
from src.helpers import Colors
from src.integrations.spotify import spotify_auth
from src.mongodb import mongo_manager
from src.webui.schemas import (
    SchemaBase,
    enabled_field,
    hidden_message_id,
    register_module,
    ui,
)

logger = logutil.init_logger(os.path.basename(__file__))


@register_module("moduleSpotify")
class SpotifyConfig(SchemaBase):
    __label__ = "Spotify"
    __description__ = "Suivi des écoutes Spotify et playlists collaboratives."
    __icon__ = "🎵"
    __category__ = "Médias & Streaming"

    enabled: bool = enabled_field()
    voteEnabled: bool = ui(
        "Votes activés",
        "boolean",
        default=False,
        description="Activer les votes sur les morceaux ajoutés.",
    )
    spotifyChannelId: str = ui(
        "Salon notifications",
        "channel",
        required=True,
        description="Salon pour les notifications d'écoute.",
    )
    spotifyPlaylistId: str | None = ui(
        "Playlist principale", "string", description="ID de la playlist Spotify principale."
    )
    spotifyNewPlaylistId: str | None = ui(
        "Playlist découvertes", "string", description="ID de la playlist de découvertes."
    )
    spotifyRecapChannelId: str | None = ui(
        "Salon message récap",
        "channel",
        description="Salon où le message de récap est publié (créé automatiquement).",
    )
    spotifyRecapPinMessage: bool = ui(
        "Épingler le message récap",
        "boolean",
        default=False,
        description="Épingler automatiquement le message de récap de la playlist.",
    )
    spotifyRecapMessageId: str | None = hidden_message_id(
        "ID message récap", "spotifyRecapChannelId"
    )
    spotifyUsers: dict[str, Any] = ui(
        "Mapping Spotify → Discord",
        "spotifymap",
        description=(
            "Associe un ID Spotify à un membre Discord. "
            "Le prénom affiché vient du mapping « Discord → Prénoms »."
        ),
    )


config, module_config, enabled_servers = load_config("moduleSpotify")

SPOTIFY_CLIENT_ID = config["spotify"]["spotifyClientId"]
SPOTIFY_CLIENT_SECRET = config["spotify"]["spotifyClientSecret"]
SPOTIFY_REDIRECT_URI = config["spotify"]["spotifyRedirectUri"]
DEV_GUILD = config["discord"]["devGuildId"]
DATA_FOLDER = config["misc"]["dataFolder"]
COOLDOWN_TIME = 1

SPOTIFY_ICON_URL = (
    "https://upload.wikimedia.org/wikipedia/commons/thumb/1/19/"
    "Spotify_logo_without_text.svg/200px-Spotify_logo_without_text.svg.png"
)

# Shared Spotipy client — built once at import time.
sp = spotify_auth()


class EmbedType(Enum):
    ADD = "add"
    DELETE = "delete"
    VOTE = "vote"
    VOTE_WIN = "vote_win"
    VOTE_LOSE = "vote_lose"
    INFOS = "infos"
    VOTE_ADD = "vote_add"


def count_votes(votes, discord2name):
    """Tally ``conserver``/``supprimer``/``menfou`` votes and resolve voter names."""
    vote_counts = {}
    users = []
    for vote in votes.values():
        vote_counts[vote] = vote_counts.get(vote, 0) + 1
    for user in votes:
        users.append(discord2name.get(user, user))
    conserver = vote_counts.get("conserver", 0)
    supprimer = vote_counts.get("supprimer", 0)
    menfou = vote_counts.get("menfou", 0)
    return conserver, supprimer, menfou, users


class VoteManager:
    """MongoDB-backed per-guild add-with-vote storage."""

    def __init__(self, guild_id, discord2name):
        self.guild_id = guild_id
        self.discord2name = discord2name
        self.collection = mongo_manager.get_guild_collection(guild_id, "addwithvotes")

    async def load_data(self):
        data = {}
        async for doc in self.collection.find():
            song_id = doc["_id"]
            data[song_id] = {k: v for k, v in doc.items() if k != "_id"}
        return data

    async def save_data(self, data):
        for song_id, song_data in data.items():
            await self.collection.update_one({"_id": song_id}, {"$set": song_data}, upsert=True)

    def count_votes(self, data, song):
        song_data = data[song]
        yes_votes = sum(1 for v in song_data["votes"].values() if v == "yes")
        no_votes = sum(1 for v in song_data["votes"].values() if v == "no")
        users = [self.discord2name.get(user, f"<@{user}>") for user in song_data["votes"]]
        return yes_votes, no_votes, users

    async def check_deadline(self, song_id):
        doc = await self.collection.find_one({"_id": song_id})
        if doc is None:
            return True
        return float(doc["deadline"]) <= datetime.now().timestamp()

    async def save_vote(self, author_id, vote, song):
        logger.info("%s voted %s to add %s", author_id, vote, song)
        await self.collection.update_one({"_id": song}, {"$set": {f"votes.{author_id}": vote}})


class ServerData:
    """Holds per-server configuration, state, and MongoDB collections."""

    def __init__(self, guild_id: str, server_config: dict):
        self.guild_id = guild_id
        self.discord2name = load_discord2name(guild_id)

        spotify_users = server_config.get("spotifyUsers", [])
        self.spotify2discord = {}
        if isinstance(spotify_users, list):
            for user in spotify_users:
                if isinstance(user, dict) and user.get("spotifyId") and user.get("discordId"):
                    self.spotify2discord[user["spotifyId"]] = user["discordId"]

        if not self.spotify2discord:
            self.spotify2discord = server_config.get("spotifyIdToDiscordId", {})

        self.channel_id = server_config.get("spotifyChannelId")
        self.playlist_id = server_config.get("spotifyPlaylistId")
        self.new_playlist_id = server_config.get("spotifyNewPlaylistId")
        self.recap_channel_id = server_config.get("spotifyRecapChannelId")
        self.recap_message_id = server_config.get("spotifyRecapMessageId")
        self.recap_pin = bool(server_config.get("spotifyRecapPinMessage", False))
        self.recap_message: Message | None = None

        db = mongo_manager.get_guild_db(guild_id)
        self.playlist_items_full = db["playlistItemsFull"]
        self.votes_db = db["votes"]

        self.vote_infos = {}
        self.snapshot = {}
        self.reminders = {}

        self.vote_manager = VoteManager(guild_id, self.discord2name)

        self.vote_infos_col = db["vote_infos"]
        self.snapshot_col = db["snapshot"]
        self.reminders_col = db["reminders"]


# Per-server data, keyed by stringified guild id.
SERVERS: dict[str, ServerData] = {}
for _guild_id in enabled_servers:
    SERVERS[str(_guild_id)] = ServerData(str(_guild_id), module_config[_guild_id])


async def embed_song(
    song: dict,
    track: dict,
    embedtype: EmbedType,
    time: datetime,
    person: str = None,
    icon: str = SPOTIFY_ICON_URL,
) -> tuple[interactions.Embed, interactions.File | None]:
    """Build a Discord embed describing a Spotify track, plus an optional preview file."""
    if not person:
        person = song.get("added_by", "")

    embed_settings = {
        EmbedType.ADD: {
            "title": "Chanson ajoutée à la playlist",
            "footer": f"Ajoutée par {person}",
            "color": Colors.SPOTIFY,
        },
        EmbedType.DELETE: {
            "title": "Chanson supprimée de la playlist",
            "footer": "",
            "color": interactions.MaterialColors.RED,
        },
        EmbedType.VOTE: {
            "title": (
                f"Vote ouvert jusqu'à "
                f"{interactions.utils.timestamp_converter(time).format(interactions.TimestampStyles.RelativeTime)}"
            ),
            "footer": "Nettoyeur de playlist",
            "color": interactions.MaterialColors.ORANGE,
        },
        EmbedType.VOTE_WIN: {
            "title": "Résultat du vote",
            "footer": "",
            "color": interactions.MaterialColors.LIME,
        },
        EmbedType.VOTE_LOSE: {
            "title": "Résultat du vote",
            "footer": "",
            "color": interactions.MaterialColors.DEEP_ORANGE,
        },
        EmbedType.INFOS: {
            "title": "Informations sur la chanson",
            "footer": "",
            "color": Colors.SPOTIFY,
        },
        EmbedType.VOTE_ADD: {
            "title": (
                f"Vote ouvert jusqu'à "
                f"{interactions.utils.timestamp_converter(time).format(interactions.TimestampStyles.RelativeTime)}"
            ),
            "footer": "",
            "color": interactions.MaterialColors.ORANGE,
        },
    }

    settings = embed_settings.get(embedtype)
    if not settings:
        raise ValueError("Invalid embed type")

    embed = interactions.Embed(title=settings["title"], color=settings["color"])
    embed.set_thumbnail(url=track["album"]["images"][0]["url"])

    track_id = track["id"]
    embed_url = f"https://open.spotify.com/embed/track/{track_id}"
    async with aiohttp.ClientSession() as session, session.get(embed_url) as response:
        content = await response.text()
    preview_match = re.search(r"\"audioPreview\":{\"url\":\"(.*?)\"}", content)

    preview_url = preview_match.group(1) if preview_match else None
    preview_text = f"\n([Écouter un extrait]({preview_url}))" if preview_url else ""

    preview_file = None
    if preview_url:
        async with aiohttp.ClientSession() as session, session.get(preview_url) as resp:
            if resp.status == 200:
                audio_data = await resp.read()
                preview_file = interactions.File(
                    file_name="preview.mp3", file=io.BytesIO(audio_data)
                )

    embed.add_field(
        name="Titre",
        value=f"[{track['name']}]({track['external_urls']['spotify']}){preview_text}",
        inline=True,
    )
    embed.add_field(
        name="Artiste",
        value=", ".join(
            f"[{artist['name']}]({artist['external_urls']['spotify']})"
            for artist in track["artists"]
        ),
        inline=True,
    )
    embed.add_field(
        name="Album",
        value=f"[{track['album']['name']}]({track['album']['external_urls']['spotify']})",
        inline=True,
    )

    if embedtype not in {EmbedType.ADD, EmbedType.VOTE_ADD}:
        embed.add_field(
            name="\u200b",
            value=(
                f"Initialement ajoutée par <@{person}>"
                f"{' (ou pas)' if person == '108967780224614400' else ''}"
            ),
            inline=False,
        )

    if embedtype == EmbedType.VOTE_ADD:
        embed.add_field(name="\u200b", value=f"Proposée par <@{person}>", inline=False)
        embed.add_field(name="Votes", value=f"1 vote (<@{person}>)", inline=False)

    if embedtype == EmbedType.VOTE:
        embed.add_field(name="Votes", value="Pas encore de votes", inline=False)
        embed.add_field(
            name="\u200b",
            value="Dashboard votes: https://drndvs.link/StatsPlaylist",
            inline=False,
        )

    if embedtype in {EmbedType.ADD, EmbedType.DELETE}:
        embed.add_field(
            name="\u200b",
            value="[Ecouter la playlist](https://drndvs.link/LaPlaylistDeLaGuilde)",
            inline=False,
        )
        embed.add_field(
            name="\u200b",
            value="[Ecouter les récents](https://drndvs.link/LesDecouvertesDeLaGuilde)",
            inline=True,
        )

    embed.set_footer(text=settings["footer"], icon_url=icon)
    embed.timestamp = time

    return embed, preview_file


async def embed_message_vote(
    keep=0,
    remove=0,
    menfou=0,
    users="",
    color=interactions.MaterialColors.ORANGE,
    description="",
):
    """Build the keep/remove/menfou tally embed attached under a vote message."""
    embed = interactions.Embed(color=color, description=description)
    embed.add_field(
        name="Conserver",
        value=f"{keep} vote{'s' if keep > 1 else ''}",
        inline=True,
    )
    embed.add_field(
        name="Supprimer",
        value=f"{remove} vote{'s' if remove > 1 else ''}",
        inline=True,
    )
    embed.add_field(
        name="Menfou",
        value=f"{menfou} vote{'s' if menfou > 1 else ''}",
        inline=True,
    )
    embed.add_field(name="\u200b", value=f"Votes de {', '.join(users)}")
    embed.add_field(name="\u200b", value="Dashboard votes: https://drndvs.link/StatsPlaylist")
    embed.set_footer(text="Nettoyeur de Playlist", icon_url=SPOTIFY_ICON_URL)
    embed.timestamp = interactions.utils.timestamp_converter(datetime.now())
    return embed


async def embed_message_vote_add(
    yes=0,
    no=0,
    users="",
    color=interactions.MaterialColors.ORANGE,
    description="",
):
    """Build the yes/no tally embed attached under an add-with-vote message."""
    embed = interactions.Embed(color=color, description=description)
    embed.add_field(
        name="Ajouter",
        value=f"{yes} vote{'s' if yes > 1 else ''}",
        inline=True,
    )
    embed.add_field(
        name="Ne pas ajouter",
        value=f"{no} vote{'s' if no > 1 else ''}",
        inline=True,
    )
    embed.add_field(name="\u200b", value=f"Votes de {', '.join(users)}")
    embed.set_footer(text="", icon_url=SPOTIFY_ICON_URL)
    embed.timestamp = interactions.utils.timestamp_converter(datetime.now())
    return embed
