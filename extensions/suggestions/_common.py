"""Config schema, logger, and shared constants for the suggestions extension."""

import os

from src.core import logging as logutil
from src.core.config import ModuleConfig
from src.discord_ext.look import Kind
from src.webui.schemas import ModuleHelp, SchemaBase, enabled_field, register_module, ui

logger = logutil.init_logger(__name__)


@register_module("moduleSuggestions")
class SuggestionsConfig(SchemaBase):
    __label__ = "Boîte à suggestions"
    __description__ = "Permet aux membres de proposer des idées avec votes et statut."
    __icon__ = "💡"
    __category__ = "Communauté"
    __help__ = ModuleHelp(
        "Propose une idée pour le serveur : les membres votent, le staff tranche.",
        ("/suggerer",),
        "Chaque suggestion reçoit des boutons 👍 / 👎 et un fil de discussion ; le staff "
        "la tranche avec ses boutons, et l'auteur reçoit la décision en MP.",
    )

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
        description=(
            "Rôle autorisé à trancher les suggestions, avec les boutons ✅ / ❌ / 🛠️ de leur "
            "carte (sinon admin uniquement). "
            "`/suggestion` n'apparaît qu'aux membres qui peuvent « Gérer les messages » : si ce "
            "rôle ne le peut pas, autorise-le dans Paramètres du serveur → Intégrations → Michel."
        ),
        role_use="staff",
    )
    discussionThreads: bool = ui(
        "Fil de discussion",
        "boolean",
        default=True,
        description=(
            "Ouvrir un fil « Discussion #12 » sous chaque suggestion. Michel doit pouvoir "
            "« Créer des fils publics » dans le salon."
        ),
    )
    anonymous: bool = ui(
        "Suggestions anonymes",
        "boolean",
        default=False,
        description="Masquer l'auteur de la suggestion dans l'embed public.",
    )


SUGGESTIONS = ModuleConfig("moduleSuggestions")

# Persistent button namespace: suggvote:{sugg_id}:{up|down}
VOTE_PREFIX = "suggvote"

# The kind of card a suggestion shows as, by status (its colour, src/discord_ext/look.py).
STATUS_KINDS: dict[str, Kind] = {
    "pending": "info",
    "approved": "success",
    "denied": "error",
    "implemented": "success",
}

STATUS_LABELS: dict[str, str] = {
    "pending": "🟦 En attente",
    "approved": "✅ Approuvée",
    "denied": "❌ Refusée",
    "implemented": "🚀 Implémentée",
}
