"""Storage manager for persisting reminders and event states."""

import json
import os
from datetime import datetime
from typing import Optional

from src import logutil
from .models import ReminderCollection, EventState, HardcoreSeason

logger = logutil.init_logger(os.path.basename(__file__))


class StorageManager:
    """Manages persistence of reminders and event states."""
    
    def __init__(self, data_folder: str):
        self.data_folder = data_folder
        self.reminders_file = os.path.join(data_folder, "journa.json")
        self.events_file = os.path.join(data_folder, "zunivers_events.json")
    
    def _ensure_folder_exists(self) -> None:
        """Ensure the data folder exists."""
        os.makedirs(self.data_folder, exist_ok=True)
    
    # Reminders storage
    
    def load_reminders(self) -> ReminderCollection:
        """Load reminders from storage."""
        try:
            with open(self.reminders_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return ReminderCollection.from_dict(data)
        except FileNotFoundError:
            logger.info("No reminders file found, starting with empty collection")
            return ReminderCollection()
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse reminders file: {e}")
            return ReminderCollection()
    
    def save_reminders(self, reminders: ReminderCollection) -> None:
        """Save reminders to storage."""
        self._ensure_folder_exists()
        try:
            with open(self.reminders_file, "w", encoding="utf-8") as f:
                json.dump(reminders.to_dict(), f, ensure_ascii=False, indent=4)
        except IOError as e:
            logger.error(f"Failed to save reminders: {e}")
    
    # Event state storage
    
    def load_event_state(self) -> EventState:
        """Load event state from storage."""
        try:
            with open(self.events_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return EventState.from_dict(data)
        except FileNotFoundError:
            logger.info("No events file found, starting with empty state")
            return EventState()
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse events file: {e}")
            return EventState()
    
    def save_event_state(self, state: EventState) -> None:
        """Save event state to storage."""
        self._ensure_folder_exists()
        try:
            with open(self.events_file, "w", encoding="utf-8") as f:
                json.dump(state.to_dict(), f, ensure_ascii=False, indent=4)
        except IOError as e:
            logger.error(f"Failed to save event state: {e}")
