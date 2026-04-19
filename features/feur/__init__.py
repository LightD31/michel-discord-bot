from features.feur.models import FeurStats
from features.feur.repository import FeurRepository
from src.webui.schemas import SchemaBase, enabled_field, register_module


@register_module("moduleFeur")
class FeurConfig(SchemaBase):
    __label__ = "Feur"
    __description__ = "Répond automatiquement « feur » aux messages se terminant par « quoi »."
    __icon__ = "😏"
    __category__ = "Communauté"

    enabled: bool = enabled_field()


__all__ = ["FeurConfig", "FeurRepository", "FeurStats"]
