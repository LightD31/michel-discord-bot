"""Pydantic models for the birthday feature."""

from datetime import datetime

import pytz
from pydantic import BaseModel, field_validator


class BirthdayEntry(BaseModel):
    user: int
    date: datetime
    timezone: str
    hideyear: bool = False
    isBirthday: bool = False

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str) -> str:
        if v not in pytz.all_timezones:
            raise ValueError(f"Invalid timezone: {v}")
        return v

    @field_validator("date", mode="before")
    @classmethod
    def validate_date(cls, v):
        if isinstance(v, str):
            try:
                return datetime.strptime(v, "%d/%m/%Y")
            except ValueError as e:
                raise ValueError("Date invalide. Format attendu : JJ/MM/AAAA") from e
        return v
