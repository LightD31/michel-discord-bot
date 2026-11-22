"""What the Spotify mixins share: the extension's client, and its state helpers.

Both mixins derive from :class:`SpotifyBase`, so mypy checks them against
what ``SpotifyExtension`` provides. The attribute is an annotation and the
methods are declared under ``TYPE_CHECKING``: nothing here exists at run
time, so the real methods (and the MRO) are those of the extension.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from interactions import Client, Task

from ._common import ServerData


class SpotifyBase:
    bot: Client
    randomvote: Task  # votes.py

    if TYPE_CHECKING:
        # __init__.py
        def get_server(self, guild_id: Any) -> ServerData: ...
        async def ensure_state(self, server: ServerData) -> None: ...
        async def save_voteinfos(self, server: ServerData) -> None: ...
        async def save_snapshot(self, server: ServerData) -> None: ...
        async def save_reminders(self, server: ServerData) -> None: ...

        # playlist.py
        async def _check_playlist_changes_for_server(self, server: ServerData) -> None: ...
