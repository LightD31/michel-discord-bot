"""Pure helpers for the Coloc/Zunivers domain — no Discord imports.

Embed builders live in :mod:`extensions.zunivers.embeds`.
"""

from datetime import datetime

from .constants import (
    LOOT_TYPE_BONUS,
    LOOT_TYPE_JOURNA,
    PARIS_TZ,
    RARITY_EMOJIS,
)


def parse_zunivers_date(date_str: str) -> datetime:
    """
    Parse a date string from the Zunivers API.
    Assumes dates without timezone are in Paris time.
    """
    dt = datetime.fromisoformat(
        date_str.replace("Z", "+00:00") if date_str.endswith("Z") else date_str
    )
    if dt.tzinfo is None:
        dt = PARIS_TZ.localize(dt)
    return dt


def format_event_items(items: list[dict], max_items_per_rarity: int = 3) -> str:
    """Format event items grouped by rarity."""
    if not items:
        return ""

    items_by_rarity: dict[int, list[str]] = {}
    for item in items:
        rarity = item.get("rarity", 1)
        if rarity not in items_by_rarity:
            items_by_rarity[rarity] = []
        items_by_rarity[rarity].append(item["name"])

    lines = []
    for rarity in sorted(items_by_rarity.keys(), reverse=True):
        rarity_emoji = RARITY_EMOJIS.get(rarity, "⭐" * rarity)
        rarity_display = rarity_emoji * rarity
        item_names = items_by_rarity[rarity][:max_items_per_rarity]
        line = f"{rarity_display} {', '.join(item_names)}"
        if len(items_by_rarity[rarity]) > max_items_per_rarity:
            line += f" (+{len(items_by_rarity[rarity]) - max_items_per_rarity} autres)"
        lines.append(line)

    return "\n".join(lines)


def image_url_needs_download(image_url: str) -> bool:
    """Check if an image URL needs to be downloaded (no extension)."""
    if not image_url:
        return False
    extensions = [".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"]
    return not any(image_url.lower().endswith(ext) for ext in extensions)


def is_journa_done(loot: dict[str, list[dict]], date_str: str) -> bool:
    """Whether the ``/loot`` history holds a ``/journa`` on ``date_str`` (``YYYY-MM-DD``)."""
    return any(entry.get("type") == LOOT_TYPE_JOURNA for entry in loot.get(date_str, []))


def journas_since_last_bonus(loot: dict[str, list[dict]]) -> int:
    """Count the ``/journa`` done since the most recent ``/bonus``.

    ``loot`` is the ``/loot/{username}`` payload: ``{"YYYY-MM-DD": [{"type", "date", ...}]}``
    covering the last 365 days. Entries are ordered by their full timestamp since
    a ``/bonus`` is usually claimed the same day as the ``/journa`` that unlocked it.
    """
    entries = sorted(
        (entry for day in loot.values() for entry in day),
        key=lambda entry: entry.get("date", ""),
    )
    count = 0
    for entry in entries:
        if entry.get("type") == LOOT_TYPE_BONUS:
            count = 0
        elif entry.get("type") == LOOT_TYPE_JOURNA:
            count += 1
    return count
