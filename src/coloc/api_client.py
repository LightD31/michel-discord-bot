"""API client for Zunivers API with retry logic."""

import asyncio
import io
import os
from typing import Optional, Any
from aiohttp import ClientSession, ClientError, ClientTimeout
from interactions import File

from src import logutil
from .constants import (
    ZUNIVERS_API_BASE,
    ZUNIVERS_EVENTS_URL,
    ZUNIVERS_HARDCORE_SEASON_URL,
    ZUNIVERS_LOOT_URL_TEMPLATE,
    ZUNIVERS_CALENDAR_URL_TEMPLATE,
    ZUNIVERS_CORPORATION_URL_TEMPLATE,
    ReminderType,
)

logger = logutil.init_logger(os.path.basename(__file__))


class ZuniversAPIError(Exception):
    """Custom exception for Zunivers API errors."""
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class ZuniversAPIClient:
    """Client for interacting with the Zunivers API."""
    
    DEFAULT_TIMEOUT = ClientTimeout(total=30)
    MAX_RETRIES = 3
    RETRY_DELAY = 1.0  # seconds
    
    def __init__(self, session: Optional[ClientSession] = None):
        self._session = session
        self._owns_session = session is None
    
    async def _get_session(self) -> ClientSession:
        """Get or create an aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = ClientSession(timeout=self.DEFAULT_TIMEOUT)
            self._owns_session = True
        return self._session
    
    async def close(self) -> None:
        """Close the session if we own it."""
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()
    
    async def _request(
        self,
        url: str,
        rule_set: Optional[ReminderType] = None,
        retries: int = MAX_RETRIES,
    ) -> Any:
        """Make a request with retry logic."""
        headers = {}
        if rule_set:
            headers["X-ZUnivers-RuleSetType"] = rule_set.value
        
        session = await self._get_session()
        last_error = None
        
        for attempt in range(retries):
            try:
                async with session.get(url, headers=headers) as response:
                    if response.status == 404:
                        return None
                    if response.status >= 400:
                        raise ZuniversAPIError(
                            f"API error: {response.status}",
                            status_code=response.status
                        )
                    return await response.json()
            except ClientError as e:
                last_error = e
                if attempt < retries - 1:
                    await asyncio.sleep(self.RETRY_DELAY * (attempt + 1))
                    logger.warning(f"Retry {attempt + 1}/{retries} for {url}: {e}")
        
        raise ZuniversAPIError(f"Failed after {retries} retries: {last_error}")
    
    async def get_current_events(self, rule_set: ReminderType) -> list[dict]:
        """Get current events for a specific rule set."""
        result = await self._request(ZUNIVERS_EVENTS_URL, rule_set)
        return result if result else []
    
    async def get_current_hardcore_season(self) -> Optional[dict]:
        """Get the current hardcore season."""
        return await self._request(
            ZUNIVERS_HARDCORE_SEASON_URL,
            ReminderType.HARDCORE
        )
    
    async def get_user_loot(self, username: str, rule_set: ReminderType) -> dict:
        """Get a user's loot data."""
        url = ZUNIVERS_LOOT_URL_TEMPLATE.format(username=username)
        result = await self._request(url, rule_set)
        return result if result else {}
    
    async def get_user_calendar(self, username: str) -> list[dict]:
        """Get a user's advent calendar data."""
        url = ZUNIVERS_CALENDAR_URL_TEMPLATE.format(username=username)
        result = await self._request(url, ReminderType.NORMAL)
        return result if isinstance(result, list) else []
    
    async def get_corporation(self, corp_id: str) -> Optional[dict]:
        """Get corporation data."""
        url = ZUNIVERS_CORPORATION_URL_TEMPLATE.format(corp_id=corp_id)
        return await self._request(url, ReminderType.NORMAL)
    
    async def download_image(self, image_url: str, filename: str = "image.webp") -> Optional[File]:
        """Download an image and return it as a Discord File object."""
        try:
            session = await self._get_session()
            async with session.get(image_url) as response:
                if response.status == 200:
                    image_data = await response.read()
                    file_obj = io.BytesIO(image_data)
                    return File(file=file_obj, file_name=filename)
        except Exception as e:
            logger.warning(f"Error downloading image from {image_url}: {e}")
        return None
    
    async def check_user_journa_done(
        self,
        username: str,
        rule_set: ReminderType,
        date_str: str,
    ) -> bool:
        """Check if a user has completed their journa for a specific date."""
        try:
            data = await self.get_user_loot(username, rule_set)
            if date_str in data:
                return len(data[date_str]) > 0
            return False
        except ZuniversAPIError:
            return False  # Assume not done if we can't check
    
    async def check_user_calendar_opened(
        self,
        username: str,
        day: int,
    ) -> bool:
        """Check if a user has opened their advent calendar for a specific day."""
        try:
            calendar_data = await self.get_user_calendar(username)
            for entry in calendar_data:
                if entry.get("day") == day and entry.get("openedAt") is not None:
                    return True
            return False
        except ZuniversAPIError:
            return False  # Assume not opened if we can't check
