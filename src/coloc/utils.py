"""Utility functions for the Coloc module."""

import os
from datetime import datetime
from typing import Optional, Tuple

from interactions import Embed, File

from src import logutil
from .constants import (
    PARIS_TZ,
    RARITY_EMOJIS,
    CURRENCY_EMOJI,
    JOURNA_HARDCORE_LINK,
    ReminderType,
)

logger = logutil.init_logger(os.path.basename(__file__))


def parse_zunivers_date(date_str: str) -> datetime:
    """
    Parse a date string from the Zunivers API.
    Assumes dates without timezone are in Paris time.
    """
    dt = datetime.fromisoformat(date_str.replace("Z", "+00:00") if date_str.endswith("Z") else date_str)
    if dt.tzinfo is None:
        dt = PARIS_TZ.localize(dt)
    return dt


def format_discord_timestamp(dt: datetime, style: str = "F") -> str:
    """Format a datetime as a Discord timestamp."""
    return f"<t:{int(dt.timestamp())}:{style}>"


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


def create_event_embed(
    event: dict,
    event_type: str,
    rule_set: ReminderType,
) -> Embed:
    """
    Create a Discord embed for an event start or end.
    
    Args:
        event: Event data from the API
        event_type: "start" or "end"
        rule_set: The rule set type
    
    Returns:
        Configured Embed object
    """
    is_start = event_type == "start"
    color = 0x00FF00 if is_start else 0xFF0000
    emoji = "🎉" if is_start else "⏰"
    action = "Nouvel événement" if is_start else "Fin d'événement"
    description = "Un nouvel événement vient de commencer !" if is_start else "L'événement vient de se terminer."
    
    embed = Embed(
        title=f"{emoji} {action} /im {event['name']}",
        description=description,
        color=color,
    )
    
    # Parse and format dates
    begin_date = parse_zunivers_date(event["beginDate"])
    end_date = parse_zunivers_date(event["endDate"])
    
    embed.add_field(
        name="📅 Période",
        value=f"Du {format_discord_timestamp(begin_date)}\nAu {format_discord_timestamp(end_date)}",
        inline=False,
    )
    
    # Add items for start events
    if is_start and event.get("items"):
        items_text = format_event_items(event["items"])
        if items_text:
            embed.add_field(
                name="🎁 Items disponibles",
                value=items_text[:1024],
                inline=False,
            )
    
    # Add cost if available
    if "balanceCost" in event:
        embed.add_field(
            name="💰 Coût",
            value=f"{event['balanceCost']} {CURRENCY_EMOJI}",
            inline=True,
        )
    
    return embed


def set_event_embed_image(embed: Embed, image_url: Optional[str], image_file: Optional[File] = None) -> None:
    """Set the image on an event embed, handling URLs without extensions."""
    if not image_url:
        return
    
    if image_file:
        embed.set_image(url="attachment://event_image.webp")
    else:
        embed.set_image(url=image_url)


def image_url_needs_download(image_url: str) -> bool:
    """Check if an image URL needs to be downloaded (no extension)."""
    if not image_url:
        return False
    extensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']
    return not any(image_url.lower().endswith(ext) for ext in extensions)


def create_season_embed(season: dict, season_type: str) -> Embed:
    """
    Create a Discord embed for a hardcore season start or end.
    
    Args:
        season: Season data from the API
        season_type: "start" or "end"
    
    Returns:
        Configured Embed object
    """
    is_start = season_type == "start"
    color = 0xFF4500 if is_start else 0x8B0000
    emoji = "🔥" if is_start else "💀"
    action = "Nouvelle saison HARDCORE" if is_start else "Fin de saison HARDCORE"
    description = (
        "Une nouvelle saison hardcore vient de commencer !"
        if is_start
        else "La saison hardcore vient de se terminer."
    )
    
    embed = Embed(
        title=f"{emoji} {action} : Saison {season['index']}",
        description=description,
        color=color,
    )
    
    # Parse and format dates
    begin_date = parse_zunivers_date(season["beginDate"])
    end_date = parse_zunivers_date(season["endDate"])
    
    embed.add_field(
        name="📅 Période de la saison",
        value=f"Du {format_discord_timestamp(begin_date)}\nAu {format_discord_timestamp(end_date)}",
        inline=False,
    )
    
    embed.set_image(url="https://zunivers.zerator.com/assets/logo-hc.webp")
    
    if is_start:
        embed.add_field(
            name="⚠️ Mode Hardcore",
            value=f"Attention ! En mode hardcore, un oubli de [/journa]({JOURNA_HARDCORE_LINK}) et on recommence tout !",
            inline=False,
        )
    
    return embed


def create_corporation_embed(data: dict, currency_emoji: str) -> Embed:
    """Create the main corporation info embed."""
    from .constants import BONUS_TYPE_NAMES, get_bonus_value_description
    
    embed = Embed(
        title=f"{data['name']} Corporation",
        description=data["description"],
        color=0x05B600,
        url=f"https://zunivers.zerator.com/corporation/{data['id']}",
    )
    
    embed.set_thumbnail(url=data["logoUrl"])
    embed.add_field(
        name="Trésorerie",
        value=f"{data['balance']} {currency_emoji}",
        inline=True,
    )
    
    members = data.get("userCorporations", [])
    member_names = ", ".join(m["user"]["discordGlobalName"] for m in members)
    embed.add_field(
        name=f"Membres ({len(members)})",
        value=member_names or "Aucun",
        inline=True,
    )
    
    for bonus in data.get("corporationBonuses", []):
        bonus_type = bonus["type"]
        level = bonus["level"]
        embed.add_field(
            name=f"{BONUS_TYPE_NAMES.get(bonus_type, bonus_type)} : Niv. {level}/4",
            value=get_bonus_value_description(bonus_type, level),
            inline=False,
        )
    
    return embed


def create_corporation_logs_embed(
    logs: list[dict],
    all_members: set[str],
    date,
    currency_emoji: str,
) -> Embed:
    """Create the corporation logs embed."""
    embed = Embed(
        title=f"Journal de la corporation pour le {date}",
        color=0x05B600,
    )
    
    active_members = set()
    log_lines = []
    
    for log in logs:
        user_name = log["user_name"]
        action = log["action"]
        amount = log.get("amount", 0)
        
        if action == "a amélioré la corporation":
            line = f"**{user_name}** {action} (**{amount}** {currency_emoji})"
        else:
            line = f"**{user_name}** {action}"
            if amount != 0:
                line += f" **{amount}** {currency_emoji}"
        
        log_lines.append(line)
        active_members.add(user_name)
    
    embed.add_field(
        name="Journal",
        value="\n".join(log_lines) if log_lines else "Aucune action aujourd'hui.",
        inline=True,
    )
    
    inactive_members = all_members - active_members
    embed.add_field(name="\u200b", value="\u200b", inline=True)
    embed.add_field(
        name="Inactifs",
        value=", ".join(inactive_members) if inactive_members else "Aucun",
        inline=True,
    )
    
    return embed
