"""Pydantic models for the birthday feature."""

import re
from datetime import datetime
from typing import Optional

import pytz
from pydantic import BaseModel, field_validator


def _safe_replace_year(dt: datetime, year: int) -> datetime:
    """Replace the year of *dt* handling Feb 29 — falls back to Mar 1."""
    try:
        return dt.replace(year=year)
    except ValueError:
        return dt.replace(year=year, month=3, day=1)


def _strip_year_from_format(date_format: str) -> str:
    """Remove year tokens (y, yy, yyyy…) from a babel/ICU date format string."""
    cleaned = re.sub(r"[,/\-.\s]*y+[,/\-.\s]*", " ", date_format)
    return cleaned.strip(" ,.-/")


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
            except ValueError:
                raise ValueError("Date invalide. Format attendu : JJ/MM/AAAA")
        return v
