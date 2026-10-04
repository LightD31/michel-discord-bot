"""Extension Spotify — gestion collaborative de playlists avec système de votes.

Permet l'ajout, la suppression et le vote de morceaux dans des playlists
Spotify partagées par serveur.

The class is assembled as a mixin composition so that each concern lives in
its own module (``playlist``, ``votes``). Shared data classes, constants, and
embed builders are in :mod:`._common`. OAuth re-authorization moved to the
Web UI dashboard (``src/webui/routes/spotify.py``).
"""

import os
from datetime import datetime

from interactions import Client, Extension, listen

from features.spotify import reattribution_map, unmapped_contributors
from src.core import logging as logutil

from ._common import SERVERS, ServerData
from .playlist import PlaylistMixin
from .votes import VotesMixin

logger = logutil.init_logger(os.path.basename(__file__))


class SpotifyExtension(Extension, PlaylistMixin, VotesMixin):
    """Discord extension combining playlist and vote behaviours."""

    def __init__(self, bot: Client):
        self.bot: Client = bot

    def get_server(self, guild_id) -> ServerData:
        """Look up the per-guild state container."""
        return SERVERS[str(guild_id)]

    @listen()
    async def on_startup(self):
        for server in SERVERS.values():
            await self.load_voteinfos(server)
            await self.load_snapshot(server)
            await self.load_reminders(server)
            await self.fix_spotify_attributions(server)
        self.check_playlist_changes.start()
        self.randomvote.start()
        self.reminder_check.start()
        self.check_for_end.start()
        self.new_titles_playlist.start()

    async def fix_spotify_attributions(self, server: ServerData):
        """Credit tracks stored under a now-mapped Spotify id to its Discord member.

        Runs at startup and after every dashboard save of the module (which
        reloads the extension), so adding someone to the Spotify → Discord
        mapping also fixes the tracks they added before.
        """
        try:
            changed = await server.repo.reattribute_added_by(
                reattribution_map(server.spotify2discord)
            )
            if changed:
                logger.info(
                    "%s titre(s)/vote(s) réattribué(s) d'un ID Spotify à un membre Discord "
                    "(serveur %s)",
                    changed,
                    server.guild_id,
                )
            unmapped = unmapped_contributors(await server.repo.added_by_counts())
            for spotify_id, count in unmapped.items():
                logger.warning(
                    "%s titre(s) attribué(s) à l'ID Spotify %r, absent du mapping "
                    "Spotify → Discord (serveur %s)",
                    count,
                    spotify_id,
                    server.guild_id,
                )
        except Exception as e:
            logger.error(
                "Réattribution des titres impossible pour le serveur %s : %s", server.guild_id, e
            )

    async def load_voteinfos(self, server: ServerData):
        doc = await server.repo.get_vote_infos()
        if doc:
            server.vote_infos = {k: v for k, v in doc.items() if k != "_id"}
        else:
            server.vote_infos = {}

    async def save_voteinfos(self, server: ServerData):
        await server.repo.save_vote_infos(server.vote_infos)

    async def load_snapshot(self, server: ServerData):
        doc = await server.repo.get_snapshot()
        if doc:
            server.snapshot = {k: v for k, v in doc.items() if k != "_id"}
        else:
            server.snapshot = {"snapshot": "", "duration": 0, "length": 0}

    async def save_snapshot(self, server: ServerData):
        await server.repo.save_snapshot(server.snapshot)

    async def load_reminders(self, server: ServerData):
        for doc in await server.repo.list_reminders():
            remind_time = datetime.strptime(doc["_id"], "%Y-%m-%d %H:%M:%S")
            server.reminders[remind_time] = set(doc["user_ids"])

    async def save_reminders(self, server: ServerData):
        docs = [
            {
                "_id": remind_time.strftime("%Y-%m-%d %H:%M:%S"),
                "user_ids": list(user_ids),
            }
            for remind_time, user_ids in server.reminders.items()
        ]
        await server.repo.replace_reminders(docs)
