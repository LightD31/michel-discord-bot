"""Zevent / Streamlabs / stats API access with small in-memory caches."""

import os
from datetime import UTC, datetime
from typing import Any

from features.zevent.backoff import RetryGate
from features.zevent.models import Participant, Show
from features.zevent.stats import (
    build_location_index,
    event_schedule,
    parse_participants,
    parse_shows,
    select_event,
)
from src.core import logging as logutil
from src.core.http import fetch

from ._common import (
    EVENT_NAME,
    EVENT_START_OVERRIDE,
    MAIN_EVENT_START_OVERRIDE,
    STATS_API_URL,
    STATS_EVENT_ID,
)

logger = logutil.init_logger(os.path.basename(__file__))


class ApiMixin:
    """Cached fetchers for the stats API and related validation helpers."""

    # Declared here (rather than inferred from ``Zevent.__init__``) so mypy
    # sees the nullable cache slots for what they are.
    _stats_event: dict | None
    _event_title: str
    _event_start: datetime
    _main_event_start: datetime
    _participant_cache: list[Participant]
    _location_index: dict[str, str]
    _planning_cache: list[Show] | None
    _event_gate: RetryGate
    _participant_gate: RetryGate
    _planning_gate: RetryGate

    async def _ensure_stats_event(self) -> dict | None:
        """Resolve (and cache) the stats-API event this tracker follows.

        With no ``zeventStatsEventId`` configured the edition is picked from the
        API listing, so a new edition is followed as soon as it is published.
        """
        if not STATS_API_URL:
            return None
        now = datetime.now()
        if not self._event_gate.ready(now):
            return self._stats_event

        try:
            events = await fetch(f"{STATS_API_URL}/events", return_type="json")
        except Exception as e:
            self._event_gate.failed(now)
            logger.error(f"Failed to fetch stats events ({self._event_gate.failures}x): {e}")
            return self._stats_event

        event = select_event(events, datetime.now(UTC), STATS_EVENT_ID or None)
        if event is None:
            # A reachable API with nothing to track is not a transient error,
            # but retrying it every cycle would still hammer the server.
            self._event_gate.failed(now)
            logger.warning("No matching event found on the stats API")
            return self._stats_event

        if not self._stats_event or self._stats_event.get("id") != event.get("id"):
            logger.info(f"Tracking stats event {event.get('name')} ({event.get('id')})")
        self._stats_event = event
        self._event_gate.succeeded(now)
        if not EVENT_NAME:
            self._event_title = str(event.get("name") or "") or self._event_title

        api_start, api_raising_start = event_schedule(event)
        if EVENT_START_OVERRIDE is None and api_start is not None:
            self._event_start = api_start
        if MAIN_EVENT_START_OVERRIDE is None and api_raising_start is not None:
            self._main_event_start = api_raising_start
        logger.info(
            f"Zevent dates: événement {self._event_start.isoformat()}, "
            f"marathon {self._main_event_start.isoformat()}"
        )
        return event

    async def _stats_event_url(self, path: str) -> str | None:
        """Build ``{base}/events/{id}/{path}``, or ``None`` when unresolvable."""
        event = await self._ensure_stats_event()
        event_id = str(event.get("id") or "") if event else ""
        if not event_id:
            return None
        return f"{STATS_API_URL}/events/{event_id}/{path}"

    async def _ensure_participant_cache(self) -> list[Participant]:
        """Refresh the participant list (LAN/remote split) from the stats API.

        ``zevent.fr`` stopped serving a ``location`` on its streamer entries, so
        this is what tells the two location embeds apart.
        """
        now = datetime.now()
        if not self._participant_gate.ready(now):
            return self._participant_cache

        url = await self._stats_event_url("donation_goals/overview")
        if not url:
            return self._participant_cache

        try:
            payload = await fetch(url, return_type="json")
        except Exception as e:
            self._participant_gate.failed(now)
            logger.error(f"Failed to fetch participants ({self._participant_gate.failures}x): {e}")
            return self._participant_cache

        participants = parse_participants(payload)
        if not participants:
            self._participant_gate.failed(now)
            logger.warning("Participants API returned no usable entry; keeping previous cache")
            return self._participant_cache

        self._participant_cache = participants
        self._location_index = build_location_index(participants)
        self._participant_gate.succeeded(now)
        lan = sum(1 for p in participants if p.location == "LAN")
        logger.info(f"Participant cache updated: {len(participants)} entries ({lan} on site)")
        return participants

    async def _ensure_planning_cache(self) -> list[Show] | None:
        """Return the cached planning, refreshing on the gate's cadence.

        The API takes a ``?day=`` filter, but the whole schedule is a dozen
        entries and the embed renders whatever comes next regardless of day —
        so one unfiltered fetch replaces a day-filtered call plus the
        empty-day fallback that used to double it.
        """
        now = datetime.now()
        if not self._planning_gate.ready(now) and self._planning_cache is not None:
            return self._planning_cache

        url = await self._stats_event_url("shows")
        if not url:
            return self._planning_cache

        try:
            shows = parse_shows(await fetch(url, return_type="json"))
        except Exception as e:
            self._planning_gate.failed(now)
            logger.error(f"Failed to update planning cache ({self._planning_gate.failures}x): {e}")
            return self._planning_cache

        self._planning_cache = shows
        self._planning_gate.succeeded(now)
        logger.info(f"Planning cache updated with {len(shows)} shows")
        return shows

    def _validate_api_data(self, data: Any, data_type: str) -> bool:
        try:
            if data_type == "planning":
                return isinstance(data, list)
            if not isinstance(data, dict):
                return False

            if data_type == "zevent":
                required_keys = ["donationAmount", "live"]
                return all(key in data for key in required_keys)
            if data_type == "streamlabs":
                return "amount_raised" in data
            return False
        except Exception:
            return False

    def _safe_get_data(self, data: Any, key_path: list[str], default: Any = None) -> Any:
        try:
            current = data
            for key in key_path:
                if isinstance(current, dict) and key in current:
                    current = current[key]
                else:
                    return default
            return current
        except Exception:
            return default

    def _is_event_started(self) -> bool:
        return datetime.now(UTC) >= self._event_start

    def _is_main_event_started(self) -> bool:
        return datetime.now(UTC) >= self._main_event_start

    async def _is_zevent_channel_live(self) -> bool:
        """True when ``twitch.tv/zevent`` currently has a live stream."""
        try:
            if not self.twitch:
                return False
            async for _ in self.twitch.get_streams(user_login=["zevent"]):
                return True
            return False
        except Exception as e:
            logger.error(f"Error checking if Zevent channel is live: {e}")
            return False

    async def _is_concert_active(self) -> bool:
        """Concert phase: event has started, main event hasn't, and Zevent chan is live."""
        if not self._is_event_started():
            return False
        if self._is_main_event_started():
            return False
        return await self._is_zevent_channel_live()

    def get_total_amount(self, data: dict, streamlabs_data: dict | None) -> tuple[str, float]:
        """Return total donations, taking the higher of Zevent API or Streamlabs."""
        try:
            total_amount = self._safe_get_data(data, ["donationAmount", "formatted"], "0 €")
            total_int = float(self._safe_get_data(data, ["donationAmount", "number"], 0))

            if streamlabs_data and "amount_raised" in streamlabs_data:
                total_from_streamlabs = streamlabs_data["amount_raised"] / 100
                logger.debug(
                    f"Total from Zevent: {total_int}, Total from Streamlabs: {total_from_streamlabs}"
                )
                if total_from_streamlabs > total_int:
                    total_int = total_from_streamlabs
                    total_amount = f"{total_int:,.2f} €".replace(",", " ")

            return total_amount, total_int
        except Exception as e:
            logger.error(f"Error calculating total amount: {e}")
            return "Erreur de calcul", 0.0
