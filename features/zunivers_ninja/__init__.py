"""ZUnivers Ninja integration — plan advice computed by a ZUnivers Ninja server.

ZUnivers Ninja (https://github.com/LightD31/ZUnivers-Ninja) runs as its own
container and exposes ``GET /api/plan/{pseudo}/discord``, a ready-to-post
message with an ``ETag`` that only changes when the advised commands do. This
package wraps that route (:mod:`.client`) and persists the last ETag posted per
pseudo (:mod:`.repository`) so the bot only posts a plan when it changed.

Free of ``interactions`` imports: the extension builds the Discord objects.
"""

from .client import (
    NinjaAttachment,
    NinjaClient,
    NinjaError,
    NinjaNotFoundError,
    NinjaPlan,
    build_plan_request,
)
from .repository import NinjaRepository

__all__ = [
    "NinjaAttachment",
    "NinjaClient",
    "NinjaError",
    "NinjaNotFoundError",
    "NinjaPlan",
    "NinjaRepository",
    "build_plan_request",
]
