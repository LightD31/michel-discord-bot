"""Deprecated: the helpers moved to :mod:`src.discord_ext` and :mod:`src.core.text`.

Re-export shim kept for one release so ``from src.helpers import …`` keeps
working. New code should import directly from the destination module:

- Embed palette / spacer / Discord timestamps → :mod:`src.discord_ext.embeds`
- Ephemeral messages / guild checks / user fetch / persistent messages
  / thread unarchive → :mod:`src.discord_ext.messages`
- Guild-scoped autocomplete / enabled-guild check → :mod:`src.discord_ext.autocomplete`
- Weighted random message picker → :mod:`src.core.text`
"""

from src.core.text import pick_weighted_message  # noqa: F401
from src.discord_ext.autocomplete import (  # noqa: F401
    guild_group_autocomplete,
    is_guild_enabled,
)
from src.discord_ext.embeds import (  # noqa: F401
    SPACER_FIELD,
    Colors,
    format_discord_timestamp,
)
from src.discord_ext.messages import (  # noqa: F401
    fetch_or_create_persistent_message,
    fetch_user_safe,
    require_guild,
    send_error,
    send_success,
    unarchive_if_thread,
)

__all__ = [
    "Colors",
    "SPACER_FIELD",
    "fetch_or_create_persistent_message",
    "fetch_user_safe",
    "format_discord_timestamp",
    "guild_group_autocomplete",
    "is_guild_enabled",
    "pick_weighted_message",
    "require_guild",
    "send_error",
    "send_success",
    "unarchive_if_thread",
]
