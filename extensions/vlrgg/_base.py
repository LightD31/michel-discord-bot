"""What the VLR.gg mixins share: the extension's state, and one another's methods.

Every mixin derives from :class:`VlrggBase`, so mypy checks each against the
state ``VlrggExtension.__init__`` sets up and the methods its siblings
provide. The attributes are annotations only, and the methods are declared
under ``TYPE_CHECKING``: nothing here exists at run time, so the real methods
(and the MRO) are those of the mixins.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from interactions import Client, Embed

from ._common import ScheduleDisplay, ServerState, TeamConfig, TeamState

if TYPE_CHECKING:
    from features.vlrgg import Competition


class VlrggBase:
    bot: Client
    _servers: dict[str, ServerState]

    if TYPE_CHECKING:
        # api.py
        @staticmethod
        def _make_vlr_match_id(match: dict[str, Any]) -> str: ...
        async def _fetch_team_schedule(
            self, tc: TeamConfig, display: ScheduleDisplay
        ) -> tuple[list[Embed], dict[str, Any], dict[str, Any]]: ...
        async def _save_live_match(
            self,
            server_id: str,
            team_name: str,
            match_id: str,
            match_data: dict[str, Any],
            channel_id: str,
            message_id: str,
        ) -> None: ...
        async def _delete_live_match(self, server_id: str, match_id: str) -> None: ...

        # embeds.py
        def _build_vlrgg_embeds(
            self,
            vlr_data: dict[str, Any],
            team: str,
            display: ScheduleDisplay,
            competition: Competition | None = None,
        ) -> list[Embed]: ...
        def _build_live_embed(
            self,
            match: dict[str, Any],
            details: dict[str, Any] | None,
            *,
            just_started: bool = False,
        ) -> Embed: ...
        def _build_final_embed(
            self, match_data: dict[str, Any], details: dict[str, Any] | None, tc: TeamConfig
        ) -> tuple[Embed, str]: ...
        def _add_details_fields(self, embed: Embed, details: dict[str, Any]) -> None: ...
        @staticmethod
        def _event_logo(details: dict[str, Any]) -> str: ...
        @staticmethod
        def _rank_line(profile: dict[str, Any]) -> str: ...

        # reminders.py
        async def _send_due_reminders(
            self, team_state: TeamState, vlr_data: dict[str, Any]
        ) -> None: ...
