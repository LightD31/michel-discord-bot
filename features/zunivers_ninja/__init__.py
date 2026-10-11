"""ZUnivers Ninja integration — plan advice computed by a ZUnivers Ninja server.

ZUnivers Ninja (https://github.com/LightD31/ZUnivers-Ninja) runs as its own
container and exposes ``GET /api/plan/{pseudo}/discord``, a ready-to-post
message for a player's best plan of the day. This package wraps that route and
builds links to a player's plan in the Ninja web UI. The ZUnivers pseudo is the
player's Discord username.

Free of ``interactions`` imports: the extension builds the Discord objects.
"""

from .client import (
    NinjaAttachment,
    NinjaClient,
    NinjaError,
    NinjaNotFoundError,
    NinjaPlan,
    build_plan_request,
    build_web_url,
)

__all__ = [
    "NinjaAttachment",
    "NinjaClient",
    "NinjaError",
    "NinjaNotFoundError",
    "NinjaPlan",
    "build_plan_request",
    "build_web_url",
]
