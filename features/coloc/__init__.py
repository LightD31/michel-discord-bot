# Coloc module package
"""
Coloc module for Zunivers integration.

This package provides:
- ZuniversAPIClient: API client with retry logic
- Data models for reminders, events, and seasons
- Pure helpers (date parsing, item formatting)
- Storage manager for persistence

Discord embed builders live in :mod:`extensions.zunivers.embeds` — this
package stays free of ``interactions`` imports.
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
    format_event_items,
    image_url_needs_download,
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
    "format_event_items",
    "image_url_needs_download",
]
