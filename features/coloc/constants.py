"""Constants for the Coloc module."""

from enum import StrEnum
from typing import Final

import pytz

# Timezone
PARIS_TZ: Final = pytz.timezone("Europe/Paris")


class ReminderType(StrEnum):
    """Types of reminders available."""

    NORMAL = "NORMAL"
    HARDCORE = "HARDCORE"


CALENDAR_URL_TEMPLATE: Final[str] = "https://zunivers.zerator.com/calendrier-festif/{username}"

# API URLs
ZUNIVERS_API_BASE: Final[str] = "https://zunivers-api.zerator.com/public"
ZUNIVERS_EVENTS_URL: Final[str] = f"{ZUNIVERS_API_BASE}/event/current"
ZUNIVERS_HARDCORE_SEASON_URL: Final[str] = f"{ZUNIVERS_API_BASE}/hardcore/season/current"
ZUNIVERS_LOOT_URL_TEMPLATE: Final[str] = f"{ZUNIVERS_API_BASE}/loot/{{username}}"
ZUNIVERS_CALENDAR_URL_TEMPLATE: Final[str] = f"{ZUNIVERS_API_BASE}/calendar/{{username}}"
ZUNIVERS_CORPORATION_URL_TEMPLATE: Final[str] = f"{ZUNIVERS_API_BASE}/corporation/{{corp_id}}"

# Default corporation ID
DEFAULT_CORPORATION_ID: Final[str] = "ce746744-e36d-4331-a0fb-399228e66ef8"

# Emoji constants
RARITY_EMOJIS: Final[dict[int, str]] = {
    1: "<:rarity1:1421467748860432507>",
    2: "<:rarity2:1421467800366612602>",
    3: "<:rarity3:1421467829139538113>",
    4: "<:rarity4:1421467859841847406>",
    5: "<:rarity5:1421467889961275402>",
}

CURRENCY_EMOJI: Final[str] = "<:eraMonnaie:1265266681291341855>"
DUST_EMOJI: Final[str] = "<:eraPoudre:1265266623217012892>"
CRYSTAL_EMOJI: Final[str] = "<:eraCristal:1265266545655812118>"

# Reminder messages
NORMAL_REMINDERS: Final[list[str]] = [
    "Tu n'as pas encore fait ton {journa} normal aujourd'hui !",
    "Hé ! N'oublie pas ton {journa} du jour !",
    "Petit rappel : ton {journa} t'attend !",
    "Il est temps de faire ton {journa} quotidien !",
    "Ton {journa} du jour t'attend !",
    "Psst... Tu as pensé à ton {journa} aujourd'hui ?",
    "Allez, c'est le moment de faire ton {journa} !",
    "N'oublie pas de valider ta journée avec ton {journa} !",
    "Ton {journa} quotidien n'attend que toi !",
    "Rappel amical : il est temps de faire ton {journa} !",
]

HARDCORE_REMINDERS: Final[list[str]] = [
    "Tu n'as pas encore fait ton {journa} hardcore aujourd'hui !",
    "Attention ! Ton {journa} hardcore du jour n'est pas fait !",
    "Ne laisse pas passer ton {journa} hardcore aujourd'hui !",
    "Rappel crucial : ton {journa} hardcore t'attend !",
    "Dernier appel pour ton {journa} hardcore du jour !",
    "URGENT : Ton {journa} hardcore n'est pas fait !",
    "Mode hardcore activé ! N'oublie pas ton {journa} !",
    "Ton aventure hardcore t'attend avec ton {journa} !",
    "Pas de repos pour les braves ! Fais ton {journa} hardcore !",
    "Le mode hardcore ne pardonne pas : fais ton {journa} maintenant !",
]

ADVENT_CALENDAR_REMINDERS: Final[list[str]] = [
    "🎄 Tu n'as pas encore ouvert ta case du [calendrier festif]({url}) aujourd'hui !",
    "🎁 N'oublie pas d'ouvrir ta case du [calendrier festif]({url}) !",
    "❄️ Une surprise t'attend dans le [calendrier festif]({url}) !",
    "🌟 Psst... ta case du jour du [calendrier festif]({url}) n'est pas ouverte !",
    "🎅 Le Père Noël attend que tu ouvres ta case du [calendrier festif]({url}) !",
]

# Corporation bonus types
BONUS_TYPE_NAMES: Final[dict[str, str]] = {
    "MEMBER_COUNT": "Taille de la corporation",
    "LOOT": "Supplément par journa",
    "RECYCLE_LORE_DUST": "Supplément de poudres créatrices au recyclage",
    "RECYCLE_LORE_FRAGMENT": "Recyclage en cristaux d'histoire au recyclage",
}

# Corporation action types
ACTION_TYPE_NAMES: Final[dict[str, str]] = {
    "LEDGER": "a donné",
    "UPGRADE": "a amélioré la corporation",
    "JOIN": "a rejoint la corporation",
    "LEAVE": "a quitté la corporation",
    "CREATE": "a créé la corporation",
}


def get_bonus_value_description(bonus_type: str, level: int) -> str:
    """Get the formatted description for a corporation bonus value."""
    cumulative_sum = sum(range(1, level + 1))

    descriptions = {
        "MEMBER_COUNT": f"+{level * 4} membres max",
        "LOOT": f"+{cumulative_sum * 10} {CURRENCY_EMOJI} par journa ou bonus",
        "RECYCLE_LORE_DUST": f"+{cumulative_sum}% {DUST_EMOJI} au recyclage",
        "RECYCLE_LORE_FRAGMENT": f"+{cumulative_sum}% {CRYSTAL_EMOJI} au recyclage",
    }
    return descriptions.get(bonus_type, f"Niveau {level}")


def format_journa_link(link: str | None) -> str:
    """Render the ``/journa`` mention used inside the reminder templates.

    The channel link is configured per guild in the Web UI; without one the
    command is rendered as plain code instead of a dead link.
    """
    return f"[/journa]({link})" if link else "`/journa`"


def get_reminder_message(reminder_type: ReminderType) -> list[str]:
    """Get the reminder templates for a specific type (format with ``journa=``)."""
    if reminder_type == ReminderType.HARDCORE:
        return HARDCORE_REMINDERS
    return NORMAL_REMINDERS


def get_advent_calendar_url(username: str) -> str:
    """Get the advent calendar URL for a specific user."""
    return CALENDAR_URL_TEMPLATE.format(username=username)
