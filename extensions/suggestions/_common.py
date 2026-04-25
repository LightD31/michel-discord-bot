"""Config schema, logger, and shared constants for the suggestions extension."""

import os

from src.core import logging as logutil
from src.core.config import load_config
from src.discord_ext.embeds import Colors
from src.webui.schemas import SchemaBase, enabled_field, register_module, ui

logger = logutil.init_logger(os.path.basename(__file__))


@register_module("moduleSuggestions")
class SuggestionsConfig(SchemaBase):
    __label__ = "Boîte à suggestions"
    __description__ = "Permet aux membres de proposer des idées avec votes et statut."
    __icon__ = "💡"
    __category__ = "Communauté"

    enabled: bool = enabled_field()
    suggestChannelId: str = ui(
        "Salon suggestions",
        "channel",
        required=True,
        description="Salon où les suggestions sont publiées.",
    )
    staffRoleId: str | None = ui(
        "Rôle staff",
        "role",
        description="Rôle autorisé à approuver/refuser les suggestions (sinon admin uniquement).",
    )
    anonymous: bool = ui(
        "Suggestions anonymes",
        "boolean",
        default=False,
        description="Masquer l'auteur de la suggestion dans l'embed public.",
    )


_, module_config, enabled_servers = load_config("moduleSuggestions")
enabled_servers_int = [int(s) for s in enabled_servers]  # type: ignore[misc]

# Persistent button namespace: suggvote:{sugg_id}:{up|down}
VOTE_PREFIX = "suggvote"

STATUS_COLORS: dict[str, int] = {
    "pending": Colors.UTIL,
    "approved": Colors.SUCCESS,
    "denied": Colors.ERROR,
    "implemented": Colors.SPOTIFY,
}

STATUS_LABELS: dict[str, str] = {
    "pending": "🟦 En attente",
    "approved": "✅ Approuvée",
    "denied": "❌ Refusée",
    "implemented": "🚀 Implémentée",
}


def get_guild_settings(guild_id: int | str) -> dict | None:
    """Return the per-guild module config, or None if disabled/missing."""
    sid = str(guild_id)
    settings = module_config.get(sid)
    if settings is None and sid.isdigit():
        settings = module_config.get(int(sid))
    return settings
