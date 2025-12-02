"""Data models for the Coloc module."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from enum import Enum

from .constants import ReminderType


@dataclass
class Reminder:
    """Represents a scheduled reminder for a user."""
    user_id: str
    remind_time: datetime
    reminder_type: ReminderType
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "user_id": self.user_id,
            "remind_time": self.remind_time.strftime("%Y-%m-%d %H:%M:%S"),
            "reminder_type": self.reminder_type.value,
        }
    
    @classmethod
    def from_dict(cls, data: dict, remind_time: datetime) -> "Reminder":
        """Create a Reminder from a dictionary."""
        return cls(
            user_id=data["user_id"],
            remind_time=remind_time,
            reminder_type=ReminderType(data["reminder_type"]),
        )


@dataclass
class ReminderCollection:
    """Manages a collection of reminders organized by time and type."""
    reminders: dict[datetime, dict[str, list[str]]] = field(default_factory=dict)
    
    def add_reminder(self, remind_time: datetime, user_id: str, reminder_type: ReminderType) -> None:
        """Add a reminder for a user."""
        if remind_time not in self.reminders:
            self.reminders[remind_time] = {"NORMAL": [], "HARDCORE": []}
        self.reminders[remind_time][reminder_type.value].append(user_id)
    
    def remove_reminder(self, remind_time: datetime, user_id: str, reminder_type: ReminderType) -> bool:
        """Remove a reminder for a user. Returns True if removed."""
        if remind_time in self.reminders:
            type_key = reminder_type.value
            if user_id in self.reminders[remind_time][type_key]:
                self.reminders[remind_time][type_key].remove(user_id)
                # Clean up empty entries
                if not self.reminders[remind_time]["NORMAL"] and not self.reminders[remind_time]["HARDCORE"]:
                    del self.reminders[remind_time]
                return True
        return False
    
    def get_user_reminders(self, user_id: str) -> list[tuple[datetime, ReminderType]]:
        """Get all reminders for a specific user."""
        result = []
        for remind_time, reminder_types in self.reminders.items():
            for type_name in ["NORMAL", "HARDCORE"]:
                if user_id in reminder_types[type_name]:
                    result.append((remind_time, ReminderType(type_name)))
        return result
    
    def get_due_reminders(self, current_time: datetime) -> list[tuple[datetime, dict[str, list[str]]]]:
        """Get all reminders that are due (past or at current time)."""
        return [
            (remind_time, reminder_types)
            for remind_time, reminder_types in self.reminders.items()
            if remind_time <= current_time
        ]
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            remind_time.strftime("%Y-%m-%d %H:%M:%S"): reminder_types
            for remind_time, reminder_types in self.reminders.items()
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "ReminderCollection":
        """Create a ReminderCollection from a dictionary."""
        collection = cls()
        for remind_time_str, reminder_types in data.items():
            remind_time = datetime.strptime(remind_time_str, "%Y-%m-%d %H:%M:%S")
            collection.reminders[remind_time] = {
                "NORMAL": reminder_types.get("NORMAL", []),
                "HARDCORE": reminder_types.get("HARDCORE", []),
            }
        return collection


@dataclass
class ZuniversEvent:
    """Represents a Zunivers event."""
    id: str
    name: str
    is_active: bool
    begin_date: datetime
    end_date: datetime
    image_url: Optional[str] = None
    balance_cost: Optional[int] = None
    items: list[dict] = field(default_factory=list)
    
    def to_state_dict(self) -> dict:
        """Convert to a state dictionary for tracking changes."""
        return {
            "name": self.name,
            "is_active": self.is_active,
            "begin_date": self.begin_date.isoformat() if isinstance(self.begin_date, datetime) else self.begin_date,
            "end_date": self.end_date.isoformat() if isinstance(self.end_date, datetime) else self.end_date,
        }
    
    @classmethod
    def from_api_response(cls, data: dict) -> "ZuniversEvent":
        """Create a ZuniversEvent from API response data."""
        return cls(
            id=data["id"],
            name=data["name"],
            is_active=data["isActive"],
            begin_date=data["beginDate"],
            end_date=data["endDate"],
            image_url=data.get("imageUrl"),
            balance_cost=data.get("balanceCost"),
            items=data.get("items", []),
        )


@dataclass
class HardcoreSeason:
    """Represents a Zunivers hardcore season."""
    id: str
    index: int
    begin_date: str
    end_date: str
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "index": self.index,
            "begin_date": self.begin_date,
            "end_date": self.end_date,
        }
    
    @classmethod
    def from_api_response(cls, data: dict) -> Optional["HardcoreSeason"]:
        """Create a HardcoreSeason from API response data."""
        if data is None:
            return None
        return cls(
            id=data["id"],
            index=data["index"],
            begin_date=data["beginDate"],
            end_date=data["endDate"],
        )
    
    @classmethod
    def from_dict(cls, data: dict) -> Optional["HardcoreSeason"]:
        """Create a HardcoreSeason from a stored dictionary."""
        if data is None:
            return None
        # Support both old format (beginDate) and new format (begin_date)
        return cls(
            id=data["id"],
            index=data["index"],
            begin_date=data.get("begin_date") or data.get("beginDate", ""),
            end_date=data.get("end_date") or data.get("endDate", ""),
        )


@dataclass 
class EventState:
    """Manages the state of events and hardcore seasons."""
    events: dict[str, dict[str, dict]] = field(default_factory=dict)  # rule_set -> event_id -> state
    hardcore_season: Optional[HardcoreSeason] = None
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "events": self.events,
            "hardcore_season": self.hardcore_season.to_dict() if self.hardcore_season else None,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "EventState":
        """Create an EventState from a dictionary."""
        hardcore_data = data.get("hardcore_season")
        return cls(
            events=data.get("events", {}),
            hardcore_season=HardcoreSeason.from_dict(hardcore_data) if hardcore_data else None,
        )


@dataclass
class CorporationLog:
    """Represents a corporation log entry."""
    user_name: str
    date: datetime
    action: str
    amount: int = 0
    
    @classmethod
    def from_api_response(cls, data: dict, action_type_names: dict[str, str]) -> "CorporationLog":
        """Create a CorporationLog from API response data."""
        action_key = data["action"]
        return cls(
            user_name=data["user"]["discordGlobalName"],
            date=datetime.strptime(data["date"], "%Y-%m-%dT%H:%M:%S.%f"),
            action=action_type_names.get(action_key) or action_key,
            amount=data.get("amount", 0),
        )
