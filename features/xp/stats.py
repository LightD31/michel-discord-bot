"""Pure shaping of XP statistics for the Web UI dashboard — no DB, no Discord.

The repository returns raw aggregation rows; these helpers turn them into the
chart-ready series the ``/api/servers/{id}/xp/stats`` route serves.
"""

from datetime import date, datetime, time, timedelta
from typing import Any

import pytz

from features.xp.leveling import calculate_level

# Window sizes (days) the dashboard offers; anything else is rejected.
ALLOWED_RANGES: tuple[int, ...] = (7, 30, 90, 365)
TOP_MEMBERS_LIMIT = 25


def stats_window(days: int, tz_name: str, now: datetime) -> tuple[date, date, datetime]:
    """Resolve a ``days``-long window ending today in ``tz_name``.

    Returns ``(first_day, last_day, since)``: the local calendar days covered
    (today included) and the UTC instant of ``first_day``'s local midnight,
    which is what the ``xp_events`` range queries match on.
    """
    tz = pytz.timezone(tz_name)
    today = now.astimezone(tz).date()
    first_day = today - timedelta(days=days - 1)
    since = tz.localize(datetime.combine(first_day, time.min)).astimezone(pytz.utc)
    return first_day, today, since


def fill_daily_series(
    activity_rows: list[dict[str, Any]],
    active_rows: list[dict[str, Any]],
    first_day: date,
    last_day: date,
) -> list[dict[str, Any]]:
    """One zero-filled entry per day from ``first_day`` to ``last_day`` inclusive.

    ``activity_rows`` come from ``XpRepository.daily_activity`` (per day and
    source), ``active_rows`` from ``XpRepository.daily_active_users``. Rows for
    days outside the window are ignored.
    """
    by_day: dict[str, dict[str, Any]] = {}
    day = first_day
    while day <= last_day:
        key = day.isoformat()
        by_day[key] = {
            "date": key,
            "message_xp": 0,
            "voice_xp": 0,
            "messages": 0,
            "voice_ticks": 0,
            "active_users": 0,
        }
        day += timedelta(days=1)

    for row in activity_rows:
        entry = by_day.get(str(row.get("day")))
        if entry is None:
            continue
        xp = int(row.get("xp") or 0)
        events = int(row.get("events") or 0)
        if row.get("source") == "voice":
            entry["voice_xp"] += xp
            entry["voice_ticks"] += events
        else:
            entry["message_xp"] += xp
            entry["messages"] += events

    for row in active_rows:
        entry = by_day.get(str(row.get("day")))
        if entry is not None:
            entry["active_users"] = int(row.get("users") or 0)

    return list(by_day.values())


def build_heatmap(rows: list[dict[str, Any]]) -> list[list[int]]:
    """7×24 XP matrix, Monday first.

    ``rows`` carry MongoDB's ``$dayOfWeek`` (1 = Sunday … 7 = Saturday), which
    is remapped so row 0 is Monday and row 6 is Sunday.
    """
    grid = [[0] * 24 for _ in range(7)]
    for row in rows:
        dow = int(row.get("dow") or 0)
        hour = int(row.get("hour") or 0)
        if not 1 <= dow <= 7 or not 0 <= hour <= 23:
            continue
        grid[(dow + 5) % 7][hour] += int(row.get("xp") or 0)
    return grid


def level_distribution(docs: list[dict[str, Any]]) -> list[dict[str, int]]:
    """Members per level, contiguous from 0 to the highest level reached.

    The level is recomputed from total XP: the stored ``lvl`` only moves on a
    level-up announcement and lags for rows created by a first award.
    """
    counts: dict[int, int] = {}
    for doc in docs:
        level, _, _ = calculate_level(int(doc.get("xp") or 0))
        counts[level] = counts.get(level, 0) + 1
    if not counts:
        return []
    return [{"level": lvl, "users": counts.get(lvl, 0)} for lvl in range(max(counts) + 1)]


def top_members(
    docs: list[dict[str, Any]],
    names: dict[str, dict[str, str]],
    limit: int = TOP_MEMBERS_LIMIT,
) -> list[dict[str, Any]]:
    """The first ``limit`` rows of ``docs`` (already sorted by XP desc), display-ready.

    ``names`` maps user id → ``{"username", "display_name"}``; a member missing
    from it falls back to their id.
    """
    result: list[dict[str, Any]] = []
    for rank, doc in enumerate(docs[:limit], start=1):
        user_id = str(doc.get("_id", ""))
        xp = int(doc.get("xp") or 0)
        level, xp_in_level, xp_to_next = calculate_level(xp)
        info = names.get(user_id, {})
        username = info.get("username") or ""
        result.append(
            {
                "rank": rank,
                "user_id": user_id,
                "display_name": info.get("display_name") or username or user_id,
                "username": username,
                "xp": xp,
                "messages": int(doc.get("msg") or 0),
                "level": level,
                "xp_in_level": xp_in_level,
                "xp_to_next": xp_to_next,
            }
        )
    return result


def summarize(docs: list[dict[str, Any]]) -> dict[str, int]:
    """All-time totals over the ``xp`` collection."""
    return {
        "members": len(docs),
        "total_xp": sum(int(d.get("xp") or 0) for d in docs),
        "total_messages": sum(int(d.get("msg") or 0) for d in docs),
    }


__all__ = [
    "ALLOWED_RANGES",
    "TOP_MEMBERS_LIMIT",
    "build_heatmap",
    "fill_daily_series",
    "level_distribution",
    "stats_window",
    "summarize",
    "top_members",
]
