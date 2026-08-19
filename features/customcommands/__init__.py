"""Per-guild custom slash commands — pure domain logic, no Discord imports.

The Discord glue (registering the commands, answering them) lives in
``extensions/customcommands``.
"""

from features.customcommands.models import CustomCommand, ParsedCommands
from features.customcommands.parser import (
    DEFAULT_DESCRIPTION,
    MAX_COMMANDS_PER_GUILD,
    MAX_DESCRIPTION_LENGTH,
    MAX_NAME_LENGTH,
    MAX_RESPONSE_LENGTH,
    MAX_RESPONSES_PER_COMMAND,
    normalize_name,
    parse_commands,
    pick_response,
)

# The per-guild config key this feature reads (``extensions/customcommands``
# is what actually calls ``load_config`` with it).
MODULE_KEY = "moduleCustomCommands"

__all__ = [
    "DEFAULT_DESCRIPTION",
    "MAX_COMMANDS_PER_GUILD",
    "MAX_DESCRIPTION_LENGTH",
    "MAX_NAME_LENGTH",
    "MAX_RESPONSES_PER_COMMAND",
    "MAX_RESPONSE_LENGTH",
    "MODULE_KEY",
    "CustomCommand",
    "ParsedCommands",
    "normalize_name",
    "parse_commands",
    "pick_response",
]
