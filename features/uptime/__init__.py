from features.uptime.models import STATUS_MAP, MonitorConfig, normalize_status
from features.uptime.repository import UptimeRepository
from src.webui.schemas import SchemaBase, enabled_field, register_module


@register_module("moduleUptime")
class UptimeConfig(SchemaBase):
    __label__ = "Uptime Kuma"
    __description__ = "Intégration Uptime Kuma pour le monitoring."
    __icon__ = "📡"
    __category__ = "Outils"

    enabled: bool = enabled_field()


__all__ = [
    "MonitorConfig",
    "STATUS_MAP",
    "UptimeConfig",
    "UptimeRepository",
    "normalize_status",
]
