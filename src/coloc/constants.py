"""Constants for the Coloc module."""

from enum import Enum
from typing import Final
import pytz

# Timezone
PARIS_TZ: Final = pytz.timezone("Europe/Paris")


class ReminderType(str, Enum):
    """Types of reminders available."""
    NORMAL = "NORMAL"
    HARDCORE = "HARDCORE"


# Discord channel/role hardcoded links
JOURNA_NORMAL_LINK: Final[str] = "https://discord.com/channels/138283154589876224/808432657838768168"
JOURNA_HARDCORE_LINK: Final[str] = "https://discord.com/channels/138283154589876224/1263861962744270958"
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
    f"Tu n'as pas encore fait ton [/journa]({JOURNA_NORMAL_LINK}) normal aujourd'hui !",
    f"Hé ! N'oublie pas ton [/journa]({JOURNA_NORMAL_LINK}) du jour !",
    f"Petit rappel : ton [/journa]({JOURNA_NORMAL_LINK}) t'attend !",
    f"Il est temps de faire ton [/journa]({JOURNA_NORMAL_LINK}) quotidien !",
    f"Ton [/journa]({JOURNA_NORMAL_LINK}) du jour t'attend !",
    f"Psst... Tu as pensé à ton [/journa]({JOURNA_NORMAL_LINK}) aujourd'hui ?",
    f"Allez, c'est le moment de faire ton [/journa]({JOURNA_NORMAL_LINK}) !",
    f"N'oublie pas de valider ta journée avec ton [/journa]({JOURNA_NORMAL_LINK}) !",
    f"Ton [/journa]({JOURNA_NORMAL_LINK}) quotidien n'attend que toi !",
    f"Rappel amical : il est temps de faire ton [/journa]({JOURNA_NORMAL_LINK}) !",
]

HARDCORE_REMINDERS: Final[list[str]] = [
    f"Tu n'as pas encore fait ton [/journa]({JOURNA_HARDCORE_LINK}) hardcore aujourd'hui !",
    f"Attention ! Ton [/journa]({JOURNA_HARDCORE_LINK}) hardcore du jour n'est pas fait !",
    f"Ne laisse pas passer ton [/journa]({JOURNA_HARDCORE_LINK}) hardcore aujourd'hui !",
    f"Rappel crucial : ton [/journa]({JOURNA_HARDCORE_LINK}) hardcore t'attend !",
    f"Dernier appel pour ton [/journa]({JOURNA_HARDCORE_LINK}) hardcore du jour !",
    f"URGENT : Ton [/journa]({JOURNA_HARDCORE_LINK}) hardcore n'est pas fait !",
    f"Mode hardcore activé ! N'oublie pas ton [/journa]({JOURNA_HARDCORE_LINK}) !",
    f"Ton aventure hardcore t'attend avec ton [/journa]({JOURNA_HARDCORE_LINK}) !",
    f"Pas de repos pour les braves ! Fais ton [/journa]({JOURNA_HARDCORE_LINK}) hardcore !",
    f"Le mode hardcore ne pardonne pas : fais ton [/journa]({JOURNA_HARDCORE_LINK}) maintenant !",
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


def get_reminder_message(reminder_type: ReminderType) -> list[str]:
    """Get the list of reminder messages for a specific type."""
    if reminder_type == ReminderType.HARDCORE:
        return HARDCORE_REMINDERS
    return NORMAL_REMINDERS


def get_advent_calendar_url(username: str) -> str:
    """Get the advent calendar URL for a specific user."""
    return CALENDAR_URL_TEMPLATE.format(username=username)
