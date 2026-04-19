"""Models and status helpers for the Uptime Kuma feature."""

from dataclasses import dataclass

STATUS_MAP: dict[int, str] = {0: "DOWN", 1: "UP", 2: "PENDING", 3: "MAINTENANCE"}


def normalize_status(status) -> str | None:
    """Map Uptime Kuma numeric status codes to their string form."""
    if status is None:
        return None
    if isinstance(status, int):
        return STATUS_MAP.get(status, str(status))
    return str(status)


@dataclass
class MonitorConfig:
    channel_id: int
    last_status: str | int | None = None
    mode: str = "detailed"
