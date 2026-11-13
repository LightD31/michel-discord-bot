"""Shared state, constants, data classes, and embed builders for the Spotify extension.

Kept separate from ``__init__.py`` so submodules (``auth``, ``playlist``,
``votes``) can import from here without triggering an import cycle through the
package root.
"""

import asyncio
import io
import os
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

import interactions

from features.spotify import SpotifyRepository
from features.spotify import search as track_search
from features.spotify.votes import tally_add_votes
from src.core import clock
from src.core import logging as logutil
from src.core.config import ModuleConfig, config_store, global_config, load_discord2name
from src.core.errors import ConfigError
from src.core.http import http_client
from src.discord_ext.embeds import Colors
from src.integrations.spotify import SPOTIFY_ERRORS, spotify_api
from src.webui.public_links import (
    PUBLIC_FLAG,
    ensure_public_token,
    public_dashboard_url,
    public_token,
)
from src.webui.schemas import (
    SchemaBase,
    enabled_field,
    register_module,
    ui,
)

logger = logutil.init_logger(__name__)


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
    voteReminderEveryDays: int = ui(
        "Rappel si pas assez de votes (jours)",
        "number",
        default=0,
        description=(
            "Quand la chanson du jour n'a pas assez de votes et que le vote est "
            "prolongé, publier un rappel dans le salon tous les X jours de "
            "prolongation. 0 = désactivé."
        ),
    )
    voteReminderRoleId: str | None = ui(
        "Rôle à mentionner dans le rappel",
        "role",
        description="Rôle mentionné dans le rappel de vote. Vide = aucune mention de rôle.",
        role_use="mention",
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
    publicDashboard: bool = ui(
        "Dashboard public",
        "boolean",
        default=False,
        description=(
            "Publier les statistiques de la playlist et des votes sur une page "
            "accessible sans connexion, via un lien secret (bouton « Page publique »)."
        ),
    )
    spotifyDashboardUrl: str | None = ui(
        "Lien du dashboard des votes",
        "url",
        description=(
            "Lien affiché sous les votes et dans le message de récap. Vide = lien "
            "vers le dashboard public s'il est activé, sinon aucun lien affiché."
        ),
    )
    spotifyPlaylistUrl: str | None = ui(
        "Lien d'écoute de la playlist",
        "url",
        description=(
            "Lien « Ecouter la playlist » ajouté aux embeds d'ajout et de "
            "suppression. Vide = champ masqué."
        ),
    )
    spotifyNewPlaylistUrl: str | None = ui(
        "Lien d'écoute des découvertes",
        "url",
        description=(
            "Lien « Ecouter les récents » ajouté aux embeds d'ajout et de "
            "suppression. Vide = champ masqué."
        ),
    )
    spotifyIconUrl: str | None = ui(
        "Icône des embeds",
        "url",
        description="Icône affichée en pied des embeds Spotify. Vide = aucune icône.",
    )
    spotifyUsers: list[dict[str, str]] = ui(
        "Mapping Spotify → Discord",
        "spotifymap",
        description=(
            "Associe un ID Spotify à un membre Discord. "
            "Le prénom affiché vient du mapping « Discord → Prénoms ». "
            "Les titres déjà ajoutés depuis ce compte Spotify lui sont réattribués "
            "à l'enregistrement."
        ),
    )


SPOTIFY = ModuleConfig("moduleSpotify")

# ``devGuildId`` is optional. Left unset it is an empty string, which
# interactions.py rejects when it converts a command's scopes to snowflakes —
# and because that conversion happens at decoration time, an unset dev guild
# used to take the whole Spotify extension down at import with nothing but an
# ExtensionLoadException in the log. ``None`` here means "no dev guild", and
# the dev-only command is simply not registered.
DEV_GUILD = str(global_config().get("discord", {}).get("devGuildId") or "").strip() or None
DATA_FOLDER = global_config()["misc"]["dataFolder"]
COOLDOWN_TIME = 1

# ``spotify_api`` (``src.integrations.spotify``) runs every spotipy call on a
# worker thread — always ``await spotify_api.<method>(...)``, never the
# synchronous client, which would block the event loop. The client is built on
# first use so the bot still starts when the token cache is missing.
# Re-authorize via the Web UI (``/api/spotify/auth/start``).


# Discord drops an autocomplete answer after 3 s: give Spotify a little less.
AUTOCOMPLETE_TIMEOUT_SECONDS = 2.5


async def autocomplete_track_choices(query: str | None) -> list[dict[str, str]]:
    """Choices for a song picker; a slow or failing Spotify yields a message, not an error."""
    if not query:
        return track_search.placeholder(track_search.PROMPT)
    try:
        result = await asyncio.wait_for(
            spotify_api.search(query, limit=10, type="track", market="FR"),
            timeout=AUTOCOMPLETE_TIMEOUT_SECONDS,
        )
    except (TimeoutError, *SPOTIFY_ERRORS) as e:
        logger.warning("Spotify search failed for autocomplete: %s", e)
        return track_search.placeholder(track_search.UNAVAILABLE)
    return track_search.track_choices(result["tracks"]["items"])


class EmbedType(Enum):
    ADD = "add"
    DELETE = "delete"
    VOTE = "vote"
    VOTE_WIN = "vote_win"
    VOTE_LOSE = "vote_lose"
    INFOS = "infos"
    VOTE_ADD = "vote_add"


class VoteManager:
    """Per-guild add-with-vote storage, persisted through :class:`SpotifyRepository`."""

    def __init__(self, repo: SpotifyRepository, guild_id: str):
        self.repo = repo
        self.guild_id = guild_id

    @property
    def discord2name(self) -> dict[str, Any]:
        return load_discord2name(self.guild_id)

    async def load_data(self):
        return await self.repo.load_addwithvote_data()

    async def save_data(self, data):
        await self.repo.save_addwithvote_data(data)

    async def delete(self, song_id):
        await self.repo.delete_addwithvote(song_id)

    def count_votes(self, data, song):
        """``(yes, no, voters)`` of the add-with-vote poll on *song*."""
        return tally_add_votes(data[song]["votes"], self.discord2name)

    async def check_deadline(self, song_id):
        doc = await self.repo.get_addwithvote_doc(song_id)
        if doc is None:
            return True
        return float(doc["deadline"]) <= clock.now().timestamp()

    async def save_vote(self, author_id, vote, song):
        logger.info("%s voted %s to add %s", author_id, vote, song)
        await self.repo.save_addwithvote_vote(author_id, vote, song)


@dataclass(frozen=True)
class SpotifyLinks:
    """Per-guild links shown in the Spotify embeds.

    Every one is configured from the Web UI — nothing is hardcoded here. An
    empty value simply means "don't show this link".
    """

    dashboard: str = ""
    playlist: str = ""
    new_playlist: str = ""
    icon: str = ""

    @classmethod
    def from_config(cls, server_config: dict, public_dashboard: str = "") -> "SpotifyLinks":
        """``public_dashboard`` stands in when no dashboard link is configured."""
        return cls(
            dashboard=str(server_config.get("spotifyDashboardUrl") or public_dashboard),
            playlist=str(server_config.get("spotifyPlaylistUrl") or ""),
            new_playlist=str(server_config.get("spotifyNewPlaylistUrl") or ""),
            icon=str(server_config.get("spotifyIconUrl") or ""),
        )


def guild_links(guild_id: str | int | None) -> SpotifyLinks:
    """Return the configured links for *guild_id* (defaults when unknown)."""
    if guild_id is None:
        return SpotifyLinks()
    server = SERVERS.get(str(guild_id))
    return server.links if server else SpotifyLinks()


class ServerData:
    """One guild's Spotify state: its repository and what the votes keep in memory.

    The settings are read from the live config at each use (``SPOTIFY.get``),
    so a dashboard save applies without reloading the extension.
    """

    def __init__(self, guild_id: str):
        self.guild_id = guild_id

        self.repo = SpotifyRepository(guild_id)

        self.vote_infos = {}
        self.snapshot = {}
        self.reminders = {}

        self.vote_manager = VoteManager(self.repo, guild_id)

    @property
    def _config(self) -> dict[str, Any]:
        return SPOTIFY.get(self.guild_id)

    @property
    def discord2name(self) -> dict[str, Any]:
        return load_discord2name(self.guild_id)

    @property
    def spotify2discord(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        users = self._config.get("spotifyUsers", [])
        for user in users if isinstance(users, list) else []:
            if isinstance(user, dict) and user.get("spotifyId") and user.get("discordId"):
                mapping[user["spotifyId"]] = user["discordId"]
        return mapping

    @property
    def channel_id(self) -> str | None:
        return self._config.get("spotifyChannelId")

    @property
    def playlist_id(self) -> str | None:
        return self._config.get("spotifyPlaylistId")

    @property
    def new_playlist_id(self) -> str | None:
        return self._config.get("spotifyNewPlaylistId")

    @property
    def recap_channel_id(self) -> str | None:
        return self._config.get("spotifyRecapChannelId")

    @property
    def recap_pin(self) -> bool:
        return bool(self._config.get("spotifyRecapPinMessage", False))

    @property
    def vote_reminder_every_days(self) -> int:
        try:
            return int(self._config.get("voteReminderEveryDays") or 0)
        except (TypeError, ValueError):
            return 0

    @property
    def vote_reminder_role_id(self) -> str:
        return str(self._config.get("voteReminderRoleId") or "")

    @property
    def links(self) -> SpotifyLinks:
        """Configured links; the public page fills in when no dashboard link is set.

        Read at use time so a token rotated from the dashboard applies at once,
        and minted on first use when the public page is switched on without a
        dashboard link, so the first embed can already link the page.
        """
        public_dashboard = ""
        if self._config.get(PUBLIC_FLAG):
            server = config_store.get().get("servers", {}).get(self.guild_id) or {}
            token = public_token(server)
            if not token and not self._config.get("spotifyDashboardUrl"):
                try:
                    token = ensure_public_token(self.guild_id)
                except ConfigError as e:
                    logger.warning(
                        "Public dashboard token unavailable for %s: %s", self.guild_id, e
                    )
            public_dashboard = public_dashboard_url(global_config(), token, "spotify")
        return SpotifyLinks.from_config(self._config, public_dashboard)


# Per-server state, keyed by stringified guild id: the guilds enabled at load
# (enabling the module in another guild reloads the extension).
SERVERS: dict[str, ServerData] = {
    guild_id: ServerData(guild_id) for guild_id in SPOTIFY.guild_ids()
}


async def embed_song(
    song: dict,
    track: dict,
    embedtype: EmbedType,
    time: datetime,
    person: str = None,
    icon: str | None = None,
    guild_id: str | int | None = None,
) -> tuple[interactions.Embed, interactions.File | None]:
    """Build a Discord embed describing a Spotify track, plus an optional preview file.

    ``guild_id`` selects the guild's configured dashboard/playlist links; the
    corresponding field is omitted when the guild left the link empty.
    """
    if not person:
        person = song.get("added_by", "")
    links = guild_links(guild_id)
    icon = icon or links.icon

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
    session = await http_client.session()
    async with session.get(embed_url) as response:
        content = await response.text()
    preview_match = re.search(r"\"audioPreview\":{\"url\":\"(.*?)\"}", content)

    preview_url = preview_match.group(1) if preview_match else None
    preview_text = f"\n([Écouter un extrait]({preview_url}))" if preview_url else ""

    preview_file = None
    if preview_url:
        async with session.get(preview_url) as resp:
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
        if links.dashboard:
            embed.add_field(
                name="\u200b",
                value=f"Dashboard votes: {links.dashboard}",
                inline=False,
            )

    if embedtype in {EmbedType.ADD, EmbedType.DELETE}:
        if links.playlist:
            embed.add_field(
                name="\u200b",
                value=f"[Ecouter la playlist]({links.playlist})",
                inline=False,
            )
        if links.new_playlist:
            embed.add_field(
                name="\u200b",
                value=f"[Ecouter les récents]({links.new_playlist})",
                inline=True,
            )

    embed.set_footer(text=settings["footer"], icon_url=icon or None)
    embed.timestamp = time

    return embed, preview_file


async def embed_message_vote(
    keep=0,
    remove=0,
    menfou=0,
    users="",
    color=interactions.MaterialColors.ORANGE,
    description="",
    guild_id: str | int | None = None,
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
    links = guild_links(guild_id)
    embed.add_field(name="\u200b", value=f"Votes de {', '.join(users)}")
    if links.dashboard:
        embed.add_field(name="\u200b", value=f"Dashboard votes: {links.dashboard}")
    embed.set_footer(text="Nettoyeur de Playlist", icon_url=links.icon or None)
    embed.timestamp = interactions.utils.timestamp_converter(clock.now())
    return embed


async def embed_message_vote_add(
    yes=0,
    no=0,
    users="",
    color=interactions.MaterialColors.ORANGE,
    description="",
    guild_id: str | int | None = None,
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
    embed.set_footer(text="", icon_url=guild_links(guild_id).icon or None)
    embed.timestamp = interactions.utils.timestamp_converter(clock.now())
    return embed
