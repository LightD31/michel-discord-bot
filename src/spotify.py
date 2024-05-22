"""
This module provides functionality for authenticating with the Spotify API and creating embed messages for Discord bots.
"""

import os
from datetime import datetime
from enum import Enum

import interactions
import spotipy

from dict import discord2name, spotify2discord
from src import logutil
from src.utils import load_config

logger = logutil.init_logger(os.path.basename(__file__))


def spotify_auth():
    """
    Authenticates the application with the Spotify API and returns a new instance of the Spotify API.

    Returns:
        spotipy.Spotify: A new instance of the Spotify API.
    """
    # Create a SpotifyOAuth object to handle authentication
    sp_oauth = spotipy.SpotifyOAuth(
        client_id=os.environ.get("SPOTIFY_CLIENT_ID"),
        redirect_uri=os.environ.get("SPOTIFY_REDIRECT_URI"),
        client_secret=os.environ.get("SPOTIFY_CLIENT_SECRET"),
        scope="playlist-modify-private playlist-read-private",
        open_browser=False,
        cache_handler=spotipy.CacheFileHandler("./.cache"),
    )

    # Check if a valid token is already cached
    token_info = sp_oauth.get_cached_token()

    # If the token is invalid or doesn't exist, prompt the user to authenticate
    if (
        not token_info
        or sp_oauth.is_token_expired(token_info)
        or not sp_oauth.validate_token(token_info)
    ):
        if token_info:
            logger.warning("Cached token has expired or is invalid.")
        # Generate the authorization URL and prompt the user to visit it
        auth_url = sp_oauth.get_authorize_url()
        logger.warning(
            "Please visit this URL to authorize the application: %s", auth_url
        )
        print(f"Please visit this URL to authorize the application: {auth_url}")

        # Wait for the user to input the response URL after authenticating
        auth_code = input("Enter the response URL: ")

        # Exchange the authorization code for an access token and refresh token
        token_info = sp_oauth.get_access_token(
            sp_oauth.parse_response_code(auth_code), as_dict=False
        )

    # Create a new instance of the Spotify API with the access token
    sp = spotipy.Spotify(auth_manager=sp_oauth, language="fr")

    return sp


class EmbedType(Enum):
    ADD = "add"
    DELETE = "delete"
    VOTE = "vote"
    VOTE_WIN = "vote_win"
    VOTE_LOSE = "vote_lose"
    INFOS = "infos"
    VOTE_ADD = "vote_add"

async def embed_song(
    song: dict,
    track: dict,
    embedtype: EmbedType,
    time: datetime,
    person: str = None,
    icon: str = "https://upload.wikimedia.org/wikipedia/commons/thumb/1/19/Spotify_logo_without_text.svg/200px-Spotify_logo_without_text.svg.png",
) -> interactions.Embed:
    """
    Creates an embed message for a Discord bot that displays information about a song.

    Args:
        song (dict): MongoDB infos
        track (dict): Spotify API info
        embedtype (EmbedType): An enum value indicating the type of message to display.
        time (datetime): A datetime object indicating the time the message was created.
        person (str, optional): The person who added the song. Defaults to None.
        icon (str, optional): The URL of the icon to use in the footer. Defaults to the Spotify logo.

    Returns:
        interactions.Embed: An embed message containing information about the song.
    """
    if not person:
        person = song.get("added_by", "")
        
    embed_settings = {
        EmbedType.ADD: {
            "title": "Chanson ajoutée à la playlist",
            "footer": f"Ajoutée par {person}",
            "color": 0x1DB954
        },
        EmbedType.DELETE: {
            "title": "Chanson supprimée de la playlist",
            "footer": "",
            "color": interactions.MaterialColors.RED
        },
        EmbedType.VOTE: {
            "title": f"Vote ouvert jusqu'à {interactions.utils.timestamp_converter(time).format(interactions.TimestampStyles.RelativeTime)}",
            "footer": "Nettoyeur de playlist",
            "color": interactions.MaterialColors.ORANGE
        },
        EmbedType.VOTE_WIN: {
            "title": "Résultat du vote",
            "footer": "",
            "color": interactions.MaterialColors.LIME
        },
        EmbedType.VOTE_LOSE: {
            "title": "Résultat du vote",
            "footer": "",
            "color": interactions.MaterialColors.DEEP_ORANGE
        },
        EmbedType.INFOS: {
            "title": "Informations sur la chanson",
            "footer": "",
            "color": 0x1DB954
        },
        EmbedType.VOTE_ADD: {
            "title": f"Vote ouvert jusqu'à {interactions.utils.timestamp_converter(time).format(interactions.TimestampStyles.RelativeTime)}",
            "footer": "",
            "color": interactions.MaterialColors.ORANGE
        }
    }

    settings = embed_settings.get(embedtype, None)
    if not settings:
        raise ValueError("Invalid embed type")

    embed = interactions.Embed(title=settings["title"], color=settings["color"])
    embed.set_thumbnail(url=track["album"]["images"][0]["url"])
    
    embed.add_field(
        name="Titre",
        value=f"[{track['name']}]({track['external_urls']['spotify']})\n([Preview]({track['preview_url']}))",
        inline=True
    )
    
    embed.add_field(
        name="Artiste",
        value=", ".join(f"[{artist['name']}]({artist['external_urls']['spotify']})" for artist in track["artists"]),
        inline=True
    )
    
    embed.add_field(
        name="Album",
        value=f"[{track['album']['name']}]({track['album']['external_urls']['spotify']})",
        inline=True
    )

    if embedtype not in {EmbedType.ADD, EmbedType.VOTE_ADD}:
        embed.add_field(
            name="\u200b",
            value=f"Initialement ajoutée par <@{person}>{' (ou pas)' if person == '108967780224614400' else ''}",
            inline=False
        )

    if embedtype == EmbedType.VOTE_ADD:
        embed.add_field(
            name="\u200b",
            value=f"Proposée par <@{person}>",
            inline=False
        )
        embed.add_field(
            name="Votes",
            value=f"1 vote (<@{person}>)",
            inline=False
        )

    if embedtype == EmbedType.VOTE:
        embed.add_field(
            name="Votes",
            value="Pas encore de votes",
            inline=False
        )
        embed.add_field(
            name="\u200b", 
            value="Dashboard votes: https://drndvs.link/StatsPlaylist",
            inline=False
        )
        
    if embedtype in {EmbedType.ADD, EmbedType.DELETE}:
        embed.add_field(
            name="\u200b",
            value="[Ecouter la playlist](https://link.drndvs.fr/LaPlaylistDeLaGuilde)",
            inline=False
        )
        embed.add_field(
            name="\u200b",
            value="[Ecouter les récents](https://link.drndvs.fr/LesDecouvertesDeLaGuilde)",
            inline=True
        )

    embed.set_footer(text=settings["footer"], icon_url=icon)
    embed.timestamp = time
    
    return embed

async def embed_message_vote(
    keep=0,
    remove=0,
    menfou=0,
    users="",
    color=interactions.MaterialColors.ORANGE,
    description="",
):
    """
    Creates an embed message for voting.

    Args:
        keep (int): Number of votes for 'keep'.
        remove (int): Number of votes for 'remove'.
        menfou (int): Number of votes for 'menfou'.
        users (str): List of users who voted.
        color (interactions.MaterialColors): Color of the embed message.

    Returns:
        interactions.Embed: The embed message.
    """
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
    embed.add_field(
        name="\u200b", value="Dashboard votes: https://drndvs.link/StatsPlaylist"
    )
    embed.set_footer(
        text="Nettoyeur de Playlist",
        icon_url="https://upload.wikimedia.org/wikipedia/commons/thumb/1/19/Spotify_logo_without_text.svg/200px-Spotify_logo_without_text.svg.png",
    )
    embed.timestamp = interactions.utils.timestamp_converter(datetime.now())
    return embed
async def embed_message_vote_add(
    yes=0,
    no=0,
    users="",
    color=interactions.MaterialColors.ORANGE,
    description="",
):
    """
    Creates an embed message for voting.

    Args:
        keep (int): Number of votes for 'keep'.
        remove (int): Number of votes for 'remove'.
        menfou (int): Number of votes for 'menfou'.
        users (str): List of users who voted.
        color (interactions.MaterialColors): Color of the embed message.

    Returns:
        interactions.Embed: The embed message.
    """
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
    embed.set_footer(
        text="",
        icon_url="https://upload.wikimedia.org/wikipedia/commons/thumb/1/19/Spotify_logo_without_text.svg/200px-Spotify_logo_without_text.svg.png",
    )
    embed.timestamp = interactions.utils.timestamp_converter(datetime.now())
    return embed

def count_votes(votes):
    """
    Counts the votes and returns a dictionary with the vote counts.

    Args:
    - votes (dict): A dictionary containing the votes.

    Returns:
    - A tuple containing the number of "conserver" votes, "supprimer" votes, and "menfou" votes, respectively.
    """
    vote_counts = {}
    users = []
    for vote in votes.values():
        if vote in vote_counts:
            vote_counts[vote] += 1
        else:
            vote_counts[vote] = 1
    for user in votes.keys():
        users.append(discord2name.get(user, user))
    conserver = vote_counts.get("conserver", 0)
    supprimer = vote_counts.get("supprimer", 0)
    menfou = vote_counts.get("menfou", 0)
    return conserver, supprimer, menfou, users


def spotifymongoformat(track, user=None):
    """
    Formats a Spotify track into a dictionary that can be stored in MongoDB.

    Args:
        track (dict): The Spotify track to format.
        user (str, optional): The user who added the track. Defaults to None.

    Returns:
        dict: The formatted track.
    """
    if track.get("track", None):
        song = {
            "_id": str(track["track"].get("id", None)),
            "added_by": str(
                user if user else spotify2discord.get(track["added_by"]["id"])
            ),
            "added_at": track.get("added_at", interactions.Timestamp.utcnow()),
            "duration_ms": track["track"]["duration_ms"],
            "name": track["track"]["name"],
            "artists": [artist.get("name") for artist in track["track"]["artists"]],
            "album": track["track"]["album"].get("name"),
        }
    else:
        song = {
            "_id": str(track.get("id", None)),
            "added_by": str(
                user if user else spotify2discord.get(track["added_by"]["id"])
            ),
            "added_at": track.get("added_at", interactions.Timestamp.utcnow()),
            "duration_ms": track["duration_ms"],
            "name": track["name"],
            "artists": [artist.get("name") for artist in track["artists"]],
            "album": track["album"].get("name"),
        }
    return song
