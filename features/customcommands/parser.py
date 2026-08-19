"""Pure parsing/validation for guild-defined slash commands (no Discord imports).

The dashboard stores a plain list of ``{"name", "description", "responses"}``
objects per guild. Everything Discord requires of a slash command (name shape,
description length, at least one response) is checked here so the extension
only ever registers entries Discord will accept — a single malformed entry is
dropped with an explanation instead of breaking the whole guild.
"""

import random
import re

from features.customcommands.models import CustomCommand, ParsedCommands

# Discord's own limits for guild application commands.
MAX_NAME_LENGTH = 32
MAX_DESCRIPTION_LENGTH = 100
MAX_RESPONSE_LENGTH = 2000
# Our own ceilings — a guild slots these alongside the bot's built-in
# commands, and Discord caps a guild at 100 application commands.
MAX_COMMANDS_PER_GUILD = 25
MAX_RESPONSES_PER_COMMAND = 25

DEFAULT_DESCRIPTION = "Commande personnalisée"

# Discord: lowercase, no spaces, letters/digits/`-`/`_`/`'` only.
_NAME_RE = re.compile(r"^[-_'\w]{1,32}$", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_name(raw: object) -> str:
    """Coerce *raw* into Discord's command-name shape (lowercase, no spaces)."""
    name = str(raw or "").strip().lower()
    return _WHITESPACE_RE.sub("-", name)


def _clean_responses(raw: object) -> list[str]:
    """Keep the usable responses of an entry, in order, de-duplicated."""
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        # Tolerate ``{"text": …}`` objects so the stored shape can grow later.
        text = item.get("text") if isinstance(item, dict) else item
        if not isinstance(text, str):
            continue
        text = text.strip()
        if not text or len(text) > MAX_RESPONSE_LENGTH or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out[:MAX_RESPONSES_PER_COMMAND]


def _parse_one(entry: object, position: int) -> tuple[CustomCommand | None, str | None]:
    """Validate a single config entry; return ``(command, error)``."""
    label = f"commande #{position}"
    if not isinstance(entry, dict):
        return None, f"{label} : entrée invalide (objet attendu)."

    name = normalize_name(entry.get("name"))
    if not name:
        return None, f"{label} : nom manquant."
    label = f"/{name}"
    if len(name) > MAX_NAME_LENGTH:
        return None, f"{label} : nom trop long (max {MAX_NAME_LENGTH} caractères)."
    if not _NAME_RE.match(name):
        return None, f"{label} : nom invalide (lettres, chiffres, `-` et `_` uniquement)."

    description = str(entry.get("description") or "").strip() or DEFAULT_DESCRIPTION
    description = description[:MAX_DESCRIPTION_LENGTH]

    responses = _clean_responses(entry.get("responses"))
    if not responses:
        return None, f"{label} : aucune réponse configurée."

    return CustomCommand(name=name, description=description, responses=responses), None


def parse_commands(
    raw_commands: object, *, reserved_names: set[str] | frozenset[str] = frozenset()
) -> ParsedCommands:
    """Read a guild's ``commands`` config list into registrable commands.

    Args:
        raw_commands: The raw value stored under ``commands`` — anything that
            isn't a list is treated as "no commands".
        reserved_names: Command names already taken in that guild's scope
            (the bot's built-in commands). Custom commands never shadow them.
    """
    if not isinstance(raw_commands, list):
        return ParsedCommands(commands=[], errors=[])

    commands: list[CustomCommand] = []
    errors: list[str] = []
    seen: set[str] = set()

    for position, entry in enumerate(raw_commands, start=1):
        command, error = _parse_one(entry, position)
        if command is None:
            errors.append(error or "")
            continue
        if command.name in seen:
            errors.append(f"/{command.name} : nom en double, entrée ignorée.")
            continue
        if command.name in reserved_names:
            errors.append(f"/{command.name} : nom déjà utilisé par une commande du bot.")
            continue
        if len(commands) >= MAX_COMMANDS_PER_GUILD:
            errors.append(
                f"/{command.name} : maximum de {MAX_COMMANDS_PER_GUILD} commandes "
                "personnalisées atteint, entrée ignorée."
            )
            continue
        seen.add(command.name)
        commands.append(command)

    return ParsedCommands(commands=commands, errors=errors)


def pick_response(command: CustomCommand, rng: random.Random | None = None) -> str:
    """Pick one of *command*'s responses at random."""
    return (rng or random).choice(command.responses)
