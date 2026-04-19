"""Domain model for a Secret Santa session."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class SecretSantaSession:
    """Represents an active Secret Santa session."""

    context_id: str
    channel_id: int
    message_id: int | None = None
    created_at: str = ""
    created_by: int = 0
    participants: list[int] = field(default_factory=list)
    is_drawn: bool = False
    budget: str | None = None
    deadline: str | None = None

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
