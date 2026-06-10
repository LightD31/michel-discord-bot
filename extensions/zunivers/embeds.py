"""Embed builders for the Zunivers extension.

Discord-facing rendering moved out of :mod:`features.coloc.utils` so the
feature package stays free of ``interactions`` imports; the pure helpers
(`parse_zunivers_date`, `format_event_items`) remain there.
"""

from interactions import Embed, File

from features.coloc.constants import (
    BONUS_TYPE_NAMES,
    CURRENCY_EMOJI,
    JOURNA_HARDCORE_LINK,
    ReminderType,
    get_bonus_value_description,
)
from features.coloc.utils import format_event_items, parse_zunivers_date
from src.discord_ext.embeds import SPACER_FIELD, Colors, format_discord_timestamp


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
    color = Colors.SUCCESS if is_start else Colors.ERROR
    emoji = "🎉" if is_start else "⏰"
    action = "Nouvel événement" if is_start else "Fin d'événement"
    description = (
        "Un nouvel événement vient de commencer !"
        if is_start
        else "L'événement vient de se terminer."
    )

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


def set_event_embed_image(
    embed: Embed, image_url: str | None, image_file: File | None = None
) -> None:
    """Set the image on an event embed, handling URLs without extensions."""
    if not image_url:
        return

    if image_file:
        embed.set_image(url="attachment://event_image.webp")
    else:
        embed.set_image(url=image_url)


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
    # Handle both API format (camelCase) and internal storage format (snake_case)
    begin_date_str = season.get("beginDate") or season.get("begin_date")
    end_date_str = season.get("endDate") or season.get("end_date")

    if begin_date_str and end_date_str:
        begin_date = parse_zunivers_date(begin_date_str)
        end_date = parse_zunivers_date(end_date_str)

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
    embed = Embed(
        title=f"{data['name']} Corporation",
        description=data["description"],
        color=Colors.COLOC,
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
        color=Colors.COLOC,
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
    embed.add_field(**SPACER_FIELD)
    embed.add_field(
        name="Inactifs",
        value=", ".join(inactive_members) if inactive_members else "Aucun",
        inline=True,
    )

    return embed
