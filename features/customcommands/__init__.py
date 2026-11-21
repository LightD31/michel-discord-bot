"""Per-guild custom slash commands — pure domain logic, no Discord imports.

The Discord glue (registering the commands, answering them) lives in
``extensions/customcommands``.
"""

from features.customcommands.cooldown import Cooldowns
from features.customcommands.models import CustomCommand, ParsedCommands, TargetMode
from features.customcommands.parser import (
    DEFAULT_DESCRIPTION,
    MAX_COMMANDS_PER_GUILD,
    MAX_COOLDOWN_SECONDS,
    MAX_DESCRIPTION_LENGTH,
    MAX_NAME_LENGTH,
    MAX_RESPONSE_LENGTH,
    MAX_RESPONSES_PER_COMMAND,
    TARGET_MODES,
    normalize_name,
    parse_commands,
    pick_response,
)
from features.customcommands.render import VARIABLES, Response, render_response

# The per-guild config key this feature reads (``extensions/customcommands``
# reads the config through a ``ModuleConfig`` on it).
MODULE_KEY = "moduleCustomCommands"

__all__ = [
    "DEFAULT_DESCRIPTION",
    "MAX_COMMANDS_PER_GUILD",
    "MAX_COOLDOWN_SECONDS",
    "MAX_DESCRIPTION_LENGTH",
    "MAX_NAME_LENGTH",
    "MAX_RESPONSES_PER_COMMAND",
    "MAX_RESPONSE_LENGTH",
    "MODULE_KEY",
    "TARGET_MODES",
    "VARIABLES",
    "Cooldowns",
    "CustomCommand",
    "ParsedCommands",
    "Response",
    "TargetMode",
    "normalize_name",
    "parse_commands",
    "pick_response",
    "render_response",
]
