from features.secretsanta.assignments import (
    generate_assignments_with_subgroups,
    generate_valid_assignments,
    is_valid_assignment,
)
from features.secretsanta.models import SecretSantaSession
from features.secretsanta.repository import SecretSantaRepository
from src.webui.schemas import SchemaBase, enabled_field, register_module


@register_module("moduleSecretSanta")
class SecretSantaConfig(SchemaBase):
    __label__ = "Secret Santa"
    __description__ = "Organisation du Secret Santa."
    __icon__ = "🎅"
    __category__ = "Événements"

    enabled: bool = enabled_field()


__all__ = [
    "SecretSantaConfig",
    "SecretSantaRepository",
    "SecretSantaSession",
    "generate_assignments_with_subgroups",
    "generate_valid_assignments",
    "is_valid_assignment",
]
