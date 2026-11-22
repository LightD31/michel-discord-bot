"""What the Zevent mixins share: the extension's state, and one another's methods.

Every mixin derives from :class:`ZeventBase`, so mypy checks each against the
state ``Zevent.__init__`` sets up and the methods its siblings provide. The
attributes are annotations only, and the methods are declared under
``TYPE_CHECKING``: nothing here exists at run time, so the real methods (and
the MRO) are those of the mixins.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from interactions import Client, Embed, Task
from twitchAPI.twitch import Twitch

from features.zevent.backoff import RetryGate
from features.zevent.history import DonationCurve
from features.zevent.models import Participant, Show
from features.zevent.velocity import DonationVelocity
from src.discord_ext.messages import GuildMessages

from ._common import StreamerInfo, ZeventGuild


class ZeventBase:
    client: Client
    twitch: Twitch | None
    messages: GuildMessages
    guilds: dict[str, ZeventGuild]
    zevent: Task

    # The last good payload, when the API fails mid-event.
    last_data_cache: dict | None
    last_update_time: datetime | None

    # The edition followed and its schedule (api.py).
    _stats_event: dict | None
    _event_title: str
    _event_start: datetime
    _main_event_start: datetime
    _event_end: datetime | None
    _participant_cache: list[Participant]
    _location_index: dict[str, str]
    _planning_cache: list[Show] | None
    _stats_events: list[dict]
    _reference_curve: DonationCurve | None
    _reference_gate: RetryGate
    _velocity: DonationVelocity
    _event_gate: RetryGate
    _participant_gate: RetryGate
    _planning_gate: RetryGate

    # Twitch presence, one poll per refresh (streams.py).
    _live_logins: set[str]
    _stream_viewers: dict[str, int]
    _stream_snapshot_time: datetime | None

    # The scheduled events (discord_event.py).
    _last_event_total: float | None
    _covers: dict[str, bytes | None]  # cover image per URL, downloaded once

    if TYPE_CHECKING:
        # api.py
        async def _ensure_participant_cache(self) -> list[Participant]: ...
        def goal_etas(self) -> dict[str, float]: ...
        async def _ensure_reference_curve(self) -> DonationCurve | None: ...
        async def reference_record(self) -> tuple[str, float] | None: ...
        def _validate_api_data(self, data: Any, data_type: str) -> bool: ...
        def _safe_get_data(self, data: Any, key_path: list[str], default: Any = None) -> Any: ...
        def _is_event_started(self) -> bool: ...
        def _is_main_event_started(self) -> bool: ...
        def get_total_amount(
            self, data: dict, streamlabs_data: dict | None
        ) -> tuple[str, float]: ...

        # streams.py
        def _get_stream_total_count(self, streams: dict, location: str) -> int: ...
        async def categorize_streams(self, streams: list[dict]) -> dict[str, dict[str, Any]]: ...
        async def get_viewers_by_location(self, streams: list[dict]) -> dict[str, str]: ...

        # embeds.py
        def create_main_embed(
            self,
            total_amount: str,
            nombre_viewers: str | None = None,
            finished: bool = False,
            concert_status: str | None = None,
        ) -> Embed: ...
        def create_location_embed(
            self,
            title: str,
            streams: dict[str, StreamerInfo],
            withlink: bool = True,
            finished: bool = False,
            viewers_count: str | None = None,
            total_count: int | None = None,
            max_chars: int | None = None,
        ) -> Embed: ...
        def create_top_donations_embed(self, streams: list[dict]) -> Embed | None: ...
        def create_planning_embed(self, shows: list[Show]) -> Embed | None: ...
        def create_donation_goals_embed(self, participants: list[Participant]) -> Embed | None: ...
        def ensure_embeds_fit_limit(
            self, embeds: list[Embed], max_size: int = ...
        ) -> list[Embed]: ...
        def remaining_embed_budget(self, embeds: list[Embed], max_size: int = ...) -> int: ...

        # discord_event.py
        async def sync_scheduled_event(
            self,
            guild: ZeventGuild,
            settings: dict,
            data: Any,
            streamlabs_data: Any,
            concert_active: bool,
            finished: bool = False,
        ) -> None: ...
