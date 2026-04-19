# Coloc module package
"""
Coloc module for Zunivers integration.

This package provides:
- ZuniversAPIClient: API client with retry logic
- Data models for reminders, events, and seasons
- Utility functions for embed creation
- Storage manager for persistence
"""

from .api_client import ZuniversAPIClient, ZuniversAPIError
from .constants import (
    CRYSTAL_EMOJI,
    CURRENCY_EMOJI,
    DUST_EMOJI,
    PARIS_TZ,
    ReminderType,
)
from .models import (
    EventState,
    HardcoreSeason,
    Reminder,
    ReminderCollection,
    ZuniversEvent,
)
from .storage import StorageManager
from .utils import (
    create_corporation_embed,
    create_corporation_logs_embed,
    create_event_embed,
    create_season_embed,
    parse_zunivers_date,
)

__all__ = [
    # Constants
    "PARIS_TZ",
    "ReminderType",
    "CURRENCY_EMOJI",
    "DUST_EMOJI",
    "CRYSTAL_EMOJI",
    # Models
    "Reminder",
    "ReminderCollection",
    "EventState",
    "ZuniversEvent",
    "HardcoreSeason",
    # API
    "ZuniversAPIClient",
    "ZuniversAPIError",
    # Storage
    "StorageManager",
    # Utils
    "parse_zunivers_date",
    "create_event_embed",
    "create_season_embed",
    "create_corporation_embed",
    "create_corporation_logs_embed",
]
