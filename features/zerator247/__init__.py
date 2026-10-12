"""ZeratoR 24/7 (the VOD rerun channel) — vote cycle tracking for !request reminders.

The 24/7 site exposes the current VOD's vote cycle at ``GET /api/now``: the
chat proposes VODs with ``!request <lien>`` during the *requests* window, then
votes on them. A ZUnivers card asks players to ``!request`` a number of times,
so subscribed members get a DM when the requests window opens.

Stays free of ``interactions`` imports; the Discord side lives in
:mod:`extensions.zunivers.zerator247`.
"""

from .client import Zerator247Client, Zerator247Error
from .models import (
    CatalogVod,
    Cycle,
    RequestSubscription,
    build_request_reminder,
    describe_cycle,
    describe_vod,
    request_command,
    should_notify,
)
from .repository import RequestReminderRepository

__all__ = [
    "CatalogVod",
    "Cycle",
    "RequestReminderRepository",
    "RequestSubscription",
    "Zerator247Client",
    "Zerator247Error",
    "build_request_reminder",
    "describe_cycle",
    "describe_vod",
    "request_command",
    "should_notify",
]
