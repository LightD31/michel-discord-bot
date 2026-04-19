from features.birthday.models import BirthdayEntry, _safe_replace_year, _strip_year_from_format
from features.birthday.repository import BirthdayRepository
from src.webui.schemas import SchemaBase, enabled_field, register_module, ui


@register_module("moduleBirthday")
class BirthdayConfig(SchemaBase):
    __label__ = "Anniversaires"
    __description__ = "Envoie des messages d'anniversaire automatiques."
    __icon__ = "🎂"
    __category__ = "Communauté"

    enabled: bool = enabled_field()
    birthdayChannelId: str = ui(
        "Salon des anniversaires",
        "channel",
        required=True,
        description="Le salon où les messages d'anniversaire seront envoyés.",
    )
    birthdayRoleId: str | None = ui(
        "Rôle anniversaire", "role", description="Rôle attribué le jour de l'anniversaire."
    )
    birthdayGuildLocale: str = ui(
        "Locale",
        "string",
        default="en_US",
        description="Locale pour le format de date (ex: fr_FR, en_US).",
    )
    birthdayMessageList: list[str] = ui(
        "Messages d'anniversaire",
        "messagelist",
        description="Liste de messages avec poids de probabilité.",
        default=["Joyeux anniversaire {mention} ! 🎉"],
        weight_field="birthdayMessageWeights",
        variables="{mention}, {age}",
    )


__all__ = [
    "BirthdayConfig",
    "BirthdayEntry",
    "BirthdayRepository",
    "_safe_replace_year",
    "_strip_year_from_format",
]
