"""Shared config schema, accessor and helpers of the welcome extension.

Config key ``moduleWelcome``.
"""

from typing import Any

from src.core import logging as logutil
from src.core.config import ModuleConfig
from src.webui.schemas import (
    ModuleHelp,
    SchemaBase,
    WeightedMessage,
    enabled_field,
    register_module,
    ui,
)

WELCOME_VARIABLES = "{mention}, {pseudo}, {serveur}, {nombre}"
LEAVE_VARIABLES = "{username}, {pseudo}, {serveur}, {nombre}"


@register_module("moduleWelcome")
class WelcomeConfig(SchemaBase):
    __label__ = "Bienvenue"
    __description__ = "Messages de bienvenue et de départ."
    __icon__ = "👋"
    __category__ = "Communauté"
    __help__ = ModuleHelp(
        "Un message pour chaque arrivée et chaque départ.",
        (),
        "Quand un membre arrive, Michel l'accueille (avec une carte si c'est activé), "
        "et chacun peut lui souhaiter la bienvenue d'un clic ; quand il part, il le dit.",
    )

    enabled: bool = enabled_field()
    welcomeChannelId: str = ui(
        "Salon de bienvenue",
        "channel",
        required=True,
        description="Salon où les messages de bienvenue sont envoyés.",
    )
    welcomeImageEnabled: bool = ui(
        "Carte d'accueil illustrée",
        "boolean",
        default=True,
        description="Joindre une image générée avec l'avatar et le pseudo du membre.",
    )
    welcomeMessageList: list[WeightedMessage] = ui(
        "Messages de bienvenue",
        "messagelist",
        description=(
            "Liste de messages avec poids de probabilité. `{mention}` mentionne le membre, "
            "`{pseudo}` le nomme, `{serveur}` est le nom du serveur et `{nombre}` "
            "le nombre de membres, nouveau venu compris."
        ),
        default=[{"text": "Bienvenue {mention} !", "weight": 1}],
        variables=WELCOME_VARIABLES,
    )
    welcomeArrivalRoleId: str | None = ui(
        "Rôle d'arrivée",
        "role",
        role_use="grant",
        description="Donné à chaque nouveau membre dès son arrivée. Vide : aucun.",
    )
    welcomeRulesChannelId: str | None = ui(
        "Salon des règles",
        "channel",
        linked_only=True,
        description="Ajoute un bouton « 📜 Règles » sous le message de bienvenue.",
    )
    welcomeRolesChannelId: str | None = ui(
        "Salon des rôles",
        "channel",
        linked_only=True,
        description=(
            "Ajoute un bouton « 🎭 Choisir mes rôles ». S'il y a un menu de rôles dans ce "
            "salon, le bouton ouvre directement le plus ancien ; sinon, le salon."
        ),
    )
    welcomeHelpButton: bool = ui(
        "Bouton « ❓ Que faire ici ? »",
        "boolean",
        default=True,
        description="Ouvre `/aide` pour le nouveau venu, en privé.",
    )
    welcomeGreetButton: bool = ui(
        "Bouton « 👋 Souhaiter la bienvenue »",
        "boolean",
        default=True,
        description=(
            "Chaque membre qui clique s'ajoute sous le message "
            "(« Léa et Hugo te souhaitent la bienvenue »), pendant 7 jours."
        ),
    )
    welcomeDmCopy: bool = ui(
        "Copie en message privé",
        "boolean",
        default=False,
        description="Envoie aussi le message de bienvenue au nouveau venu, en MP.",
    )
    leaveMessageList: list[WeightedMessage] = ui(
        "Messages de départ",
        "messagelist",
        description="Liste de messages de départ avec poids de probabilité.",
        default=[{"text": "{username} nous a quittés.", "weight": 1}],
        variables=LEAVE_VARIABLES,
    )


logger = logutil.init_logger("extensions.welcome")

WELCOME = ModuleConfig("moduleWelcome")


def message_values(member: Any, guild: Any) -> dict[str, Any]:
    """What a welcome or leave message fills in, for *member* of *guild*."""
    count = getattr(guild, "member_count", None)
    return {
        "mention": member.mention,
        "username": member.username,
        "pseudo": getattr(member, "display_name", None) or member.username,
        "serveur": getattr(guild, "name", "") or "",
        "nombre": count if count else "",
    }


__all__ = [
    "LEAVE_VARIABLES",
    "WELCOME",
    "WELCOME_VARIABLES",
    "WelcomeConfig",
    "logger",
    "message_values",
]
