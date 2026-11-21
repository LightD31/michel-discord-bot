"""« Quand ? » as a member types it: "dans 2h", "demain 18h30", "samedi 9h", "24/12 20h".

Used by the "⏰ Me le rappeler" form (P4, B1). Wall times are read on the
community's clock (:data:`src.core.clock.LOCAL_TZ`); the result is a UTC
instant, always in the future. A day without an hour means 9:00, "ce soir"
20:00. A bare hour already past today is tomorrow's, a weekday today's only
while its hour is still ahead.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from features.secretsanta.dates import MAX_DAYS_AHEAD, MONTH_NAMES, WEEKDAYS, parse_exchange_date
from src.core import clock

DEFAULT_HOUR = 9
EVENING_HOUR = 20
EXAMPLES = "dans 2h, demain 18h30, samedi 9h, 24/12 20h"

_UNITS = {
    "m": 1,
    "min": 1,
    "mn": 1,
    "minute": 1,
    "minutes": 1,
    "h": 60,
    "heure": 60,
    "heures": 60,
    "j": 1440,
    "jour": 1440,
    "jours": 1440,
    "semaine": 10080,
    "semaines": 10080,
}
_RELATIVE = re.compile(r"(?:dans\s+)?(\d{1,4})\s*([a-z]+)\.?(?:\s*(\d{1,2}))?")
_TIME = re.compile(r"(?:^|\s)(?:à\s*|a\s+|vers\s+)?(\d{1,2})\s*(?:h|:)\s*(\d{2})?$")
_NAMED_TIMES = {"midi": (12, 0), "minuit": (0, 0)}


def unreadable(text: str) -> ValueError:
    return ValueError(f"Je n'ai pas compris « {text.strip()} ». Exemples : {EXAMPLES}.")


def _relative(text: str, now: datetime) -> datetime | None:
    """ "dans 2h", "dans 2h30", "30 min", "3 jours": a delay from now.

    Hours need "dans": a bare "18h30" is an hour of the day.
    """
    match = _RELATIVE.fullmatch(text)
    if not match or match[2] not in _UNITS:
        return None
    if _UNITS[match[2]] == 60 and not text.startswith("dans"):
        return None
    minutes = int(match[1]) * _UNITS[match[2]]
    if match[3]:
        if _UNITS[match[2]] != 60:  # "2h30" only
            return None
        minutes += int(match[3])
    if minutes <= 0:
        return None
    return now + timedelta(minutes=minutes)


def _split_time(text: str) -> tuple[str, tuple[int, int] | None]:
    """The day part and the hour at the end of *text*, if it has one."""
    for word, named in _NAMED_TIMES.items():
        if text == word or text.endswith((f" {word}", f" à {word}")):
            return text.removesuffix(word).strip().removesuffix(" à").strip(), named
    match = _TIME.search(text)
    if not match:
        return text, None
    hour, minute = int(match[1]), int(match[2] or 0)
    return text[: match.start()].strip().removesuffix(" à").strip(), (hour, minute)


def _day(text: str, today: date) -> tuple[date, bool] | None:
    """The day *text* names and whether it was a weekday (which can mean next week)."""
    if text in ("", "aujourd'hui", "aujourdhui", "ce soir", "ce matin", "cet après-midi"):
        return today, False
    if text == "demain":
        return today + timedelta(days=1), False
    if text in ("après-demain", "apres-demain"):
        return today + timedelta(days=2), False
    name = text.removeprefix("le ").removeprefix("ce ").removesuffix(" prochain").strip()
    if name in WEEKDAYS:
        return today + timedelta(days=(WEEKDAYS.index(name) - today.weekday()) % 7), True
    try:
        return parse_exchange_date(text, today), False
    except ValueError:
        return None


def parse_when(text: str, now: datetime) -> datetime:
    """The instant *text* names, as seen at *now*; ``ValueError`` (French) otherwise."""
    cleaned = " ".join(text.lower().replace("’", "'").split())
    if not cleaned:
        raise unreadable(text)
    relative = _relative(cleaned, now)
    if relative is not None:
        when = relative
    else:
        day_text, hour = _split_time(cleaned)
        today = clock.to_local(now).date()
        found = _day(day_text, today)
        if found is None:
            raise unreadable(text)
        day, weekday = found
        if hour is None:
            hour = (EVENING_HOUR, 0) if day_text == "ce soir" else (DEFAULT_HOUR, 0)
        if not (0 <= hour[0] <= 23 and 0 <= hour[1] <= 59):
            raise unreadable(text)
        when = clock.at_local(day, *hour)
        if when <= now and (weekday or (day_text == "" and day == today)):
            # "samedi 9h" on a Saturday at 10:00, or "8h" after 8:00: the next one.
            when = clock.at_local(day + timedelta(days=7 if weekday else 1), *hour)
    if when <= now:
        raise ValueError("Ce moment est déjà passé : choisis une heure à venir.")
    if when - now > timedelta(days=MAX_DAYS_AHEAD):
        raise ValueError("C'est trop loin : vérifie la date.")
    return when


def reading(when: datetime, now: datetime) -> str:
    """How *when* reads back to the member: « samedi 21 novembre à 09:00 ».

    The year is added when it isn't *now*'s, so "24/12" typed in late
    December reads as next year's.
    """
    local = clock.to_local(when)
    day = "1er" if local.day == 1 else str(local.day)
    text = f"{WEEKDAYS[local.weekday()]} {day} {MONTH_NAMES[local.month - 1]}"
    if local.year != clock.to_local(now).year:
        text += f" {local.year}"
    return f"{text} à {local:%H:%M}"


__all__ = ["EXAMPLES", "parse_when", "reading"]
