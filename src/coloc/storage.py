"""Storage manager for persisting reminders and event states using MongoDB."""

import os

from src import logutil
from src.mongodb import mongo_manager
from .models import EventState, HardcoreSeason, ReminderCollection

logger = logutil.init_logger(os.path.basename(__file__))


class StorageManager:
    """Manages persistence of reminders and event states via MongoDB."""
    
    def __init__(self, data_folder: str, guild_id: str | None = None):
        self.data_folder = data_folder  # kept for backward compat, no longer used
        self._guild_id = guild_id
    
    @property
    def _reminders_col(self):
        return mongo_manager.get_guild_collection(self._guild_id, "coloc_reminders")

    @property
    def _events_col(self):
        return mongo_manager.get_guild_collection(self._guild_id, "coloc_events")
    
    # Reminders storage
    
    async def load_reminders(self) -> ReminderCollection:
        """Load reminders from MongoDB."""
        try:
            doc = await self._reminders_col.find_one({"_id": "current"})
            if doc:
                data = doc.get("data", {})
                return ReminderCollection.from_dict(data)
            logger.info("No reminders document found, starting with empty collection")
            return ReminderCollection()
        except Exception as e:
            logger.error("Failed to load reminders: %s", e)
            return ReminderCollection()
    
    async def save_reminders(self, reminders: ReminderCollection) -> None:
        """Save reminders to MongoDB."""
        try:
            await self._reminders_col.update_one(
                {"_id": "current"},
                {"$set": {"data": reminders.to_dict()}},
                upsert=True,
            )
        except Exception as e:
            logger.error("Failed to save reminders: %s", e)
    
    # Event state storage
    
    async def load_event_state(self) -> EventState:
        """Load event state from MongoDB."""
        try:
            doc = await self._events_col.find_one({"_id": "current"})
            if doc:
                data = doc.get("data", {})
                return EventState.from_dict(data)
            logger.info("No events document found, starting with empty state")
            return EventState()
        except Exception as e:
            logger.error("Failed to load event state: %s", e)
            return EventState()
    
    async def save_event_state(self, state: EventState) -> None:
        """Save event state to MongoDB."""
        try:
            await self._events_col.update_one(
                {"_id": "current"},
                {"$set": {"data": state.to_dict()}},
                upsert=True,
            )
        except Exception as e:
            logger.error("Failed to save event state: %s", e)
