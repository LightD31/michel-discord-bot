"""Pydantic models for the birthday feature."""

from datetime import UTC, datetime
from zoneinfo import available_timezones

from pydantic import BaseModel, field_validator

# The IANA zone names a member can pick (from the tzdata package).
TIMEZONES: frozenset[str] = frozenset(available_timezones())


class BirthdayEntry(BaseModel):
    user: int
    date: datetime
    timezone: str
    hideyear: bool = False
    isBirthday: bool = False

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str) -> str:
        if v not in TIMEZONES:
            raise ValueError(f"Invalid timezone: {v}")
        return v

    @field_validator("date")
    @classmethod
    def date_at_utc_midnight(cls, v: datetime) -> datetime:
        """A birth date is a calendar day: kept as that day at 00:00 UTC, never converted.

        MongoDB hands dates back as aware UTC datetimes; a naive one from a
        form gets the same shape, so a saved entry reads back equal.
        """
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v

    @field_validator("date", mode="before")
    @classmethod
    def validate_date(cls, v):
        if isinstance(v, str):
            try:
                return datetime.strptime(v, "%d/%m/%Y")
            except ValueError as e:
                raise ValueError("Date invalide. Format attendu : JJ/MM/AAAA") from e
        return v
