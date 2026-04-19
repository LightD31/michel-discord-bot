"""Zevent / Streamlabs / planning API access with small in-memory caches."""

import os
from datetime import UTC, date, datetime
from typing import Any

from src import logutil
from src.utils import fetch

from ._common import (
    EVENT_START_DATE,
    MAIN_EVENT_START_DATE,
    PLANNING_API_URL,
    STREAMERS_API_URL,
)

logger = logutil.init_logger(os.path.basename(__file__))


class ApiMixin:
    """Cached fetchers for streamer/planning APIs and related validation helpers."""

    def _get_planning_day(self, now_date: date) -> str:
        """Day to request planning for: pin to event start until it's reached."""
        zevent_start = EVENT_START_DATE.date()
        target = zevent_start if now_date < zevent_start else now_date
        return target.strftime("%Y-%m-%d")

    async def _ensure_streamer_cache(self):
        """Populate ``self._streamer_cache`` from STREAMERS_API_URL (24 h TTL)."""
        try:
            if (
                self._streamer_cache_time
                and datetime.now() - self._streamer_cache_time < self.STREAMER_CACHE_TTL
            ):
                return

            data = await fetch(STREAMERS_API_URL, return_type="json")
            if not isinstance(data, list):
                logger.warning("Streamers API returned unexpected format; skipping cache update")
                return

            mapping = {}
            for entry in data:
                try:
                    sid = entry.get("id")
                    pid = entry.get("participation_id") or entry.get("participationId")
                    name = entry.get("name") or entry.get("display_name") or entry.get("login")
                    if sid and name:
                        mapping[sid] = name
                    if pid and name:
                        mapping[pid] = name
                except Exception:
                    continue

            if mapping:
                self._streamer_cache = mapping
                self._streamer_cache_time = datetime.now()
                logger.info(f"Streamer cache updated with {len(mapping)} entries")
        except Exception as e:
            logger.error(f"Failed to update streamer cache: {e}")

    async def _ensure_planning_cache(self, target_day: str) -> list | None:
        """Return cached planning events for ``target_day``, refreshing after TTL."""
        try:
            if (
                self._planning_cache_time
                and datetime.now() - self._planning_cache_time < self.PLANNING_CACHE_TTL
                and self._planning_cache is not None
            ):
                return self._planning_cache

            planning_url = f"{PLANNING_API_URL}?day={target_day}"
            planning_data = await fetch(planning_url, return_type="json")

            if isinstance(planning_data, list):
                self._planning_cache = planning_data
                self._planning_cache_time = datetime.now()
                logger.info(
                    f"Planning cache updated with {len(planning_data)} events for {target_day}"
                )
                return planning_data
            logger.warning(f"Planning API returned unexpected format for {target_day}")
            return None
        except Exception as e:
            logger.error(f"Failed to update planning cache: {e}")
            return None

    def _validate_api_data(self, data: Any, data_type: str) -> bool:
        try:
            if not isinstance(data, dict):
                return False

            if data_type == "zevent":
                required_keys = ["donationAmount", "live"]
                return all(key in data for key in required_keys)
            if data_type == "planning":
                return "data" in data and isinstance(data["data"], list)
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
        return datetime.now(UTC) >= EVENT_START_DATE

    def _is_main_event_started(self) -> bool:
        return datetime.now(UTC) >= MAIN_EVENT_START_DATE

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
