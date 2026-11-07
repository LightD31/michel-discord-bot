"""Optional error reporting to Sentry or GlitchTip (any Sentry-compatible server).

Off unless the global section ``errorReporting`` has a ``dsn``. Then the
process reports, with the log context (guild, command or task, correlation
id) as tags:

- every record logged at ERROR *with an exception attached* (``exc_info``):
  an interaction that raised (``extensions/errors.py``), a background task
  that died (``src.core.tasks.spawn``), a failed backup… Plain error lines
  (an API answering 503, a guild misconfigured) stay in the logs only: they
  are expected, and would bury the real bugs;
- every failed ``Task`` run, through :func:`capture`
  (``src.discord_ext.task_health``), which interactions.py logs without the
  exception.

WARNING records and above travel with an event as breadcrumbs. Nothing else
is collected: no local variables of the stack frames (they can hold tokens or
message text), no user details (``send_default_pii`` off), no performance
tracing, and no automatic integration (they would add database queries and
HTTP calls as breadcrumbs).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.core import log_context

FLUSH_TIMEOUT_SECONDS = 2.0

_enabled = False


def _before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    record = hint.get("log_record")
    if record is not None and not record.exc_info:
        return None  # an error line without an exception: expected, logged only
    context = getattr(record, "log_context", None) if record is not None else None
    tags = context if isinstance(context, dict) else log_context.current()
    if tags:
        event.setdefault("tags", {}).update(tags)
    return event


def init(settings: dict[str, Any]) -> bool:
    """Start reporting if *settings* (``config.errorReporting``) has a DSN; return whether it did."""
    global _enabled
    dsn = str(settings.get("dsn") or "").strip()
    if not dsn:
        return False
    import sentry_sdk
    from sentry_sdk.integrations.logging import LoggingIntegration

    sentry_sdk.init(
        dsn=dsn,
        environment=str(settings.get("environment") or "production"),
        send_default_pii=False,
        include_local_variables=False,
        default_integrations=False,
        auto_enabling_integrations=False,
        integrations=[LoggingIntegration(level=logging.WARNING, event_level=logging.ERROR)],
        before_send=_before_send,  # type: ignore[arg-type]
    )
    _enabled = True
    return True


def capture(error: BaseException) -> None:
    """Report *error* now, tagged with the current log context. No-op when off."""
    if not _enabled:
        return
    import sentry_sdk

    sentry_sdk.capture_exception(error)


async def flush() -> None:
    """Send what is queued before the process exits (a ``GracefulShutdown`` closer)."""
    if not _enabled:
        return
    import sentry_sdk

    await asyncio.to_thread(sentry_sdk.flush, FLUSH_TIMEOUT_SECONDS)


def enabled() -> bool:
    return _enabled


__all__ = ["capture", "enabled", "flush", "init"]
