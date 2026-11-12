"""Logger factory: colored text by default, JSON lines on demand.

``MICHEL_LOG_FORMAT=json`` writes one JSON object per line (for Loki, Elastic,
``jq``…) instead of the colored text. Either way, every record carries what
the bot was doing when it was logged (:mod:`src.core.log_context`: guild,
command or task, correlation id): JSON fields, or a ``[ref=… guild=…]``
suffix on a text line. ``MICHEL_DEBUG=1`` lowers the level to DEBUG.
"""

import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

from src.core import log_context
from src.core.errors import is_expected_failure

# DEBUG flag, sourced from the ``MICHEL_DEBUG`` environment variable.
DEBUG = os.environ.get("MICHEL_DEBUG", "").lower() in ("1", "true", "yes", "on")
JSON_LOGS = os.environ.get("MICHEL_LOG_FORMAT", "").strip().lower() == "json"

# Short names for the context suffix of a text line.
_TEXT_KEYS = {"correlation_id": "ref", "guild_id": "guild", "command": "cmd", "task": "task"}


def _context_record_factory() -> Any:
    """Wrap the current record factory so each record snapshots the log context."""
    base = logging.getLogRecordFactory()
    if getattr(base, "_michel_context", False):
        return base

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = base(*args, **kwargs)
        record.log_context = log_context.current()
        return record

    factory._michel_context = True  # type: ignore[attr-defined]
    return factory


logging.setLogRecordFactory(_context_record_factory())


def _context_of(record: logging.LogRecord) -> dict[str, str]:
    context = getattr(record, "log_context", None)
    return context if isinstance(context, dict) else {}


class JsonFormatter(logging.Formatter):
    """One JSON object per record: time, level, logger, message, context, exception."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **_context_of(record),
        }
        if DEBUG:
            payload["line"] = f"{record.pathname}:{record.lineno}"
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        elif record.exc_text:
            payload["exception"] = record.exc_text
        return json.dumps(payload, ensure_ascii=False, default=str)


class CustomFormatter(logging.Formatter):
    """Custom formatter with ANSI color codes per log level."""

    grey = "\x1b[38;1m"
    green = "\x1b[42;1m"
    yellow = "\x1b[43;1m"
    red = "\x1b[41;1m"
    bold_red = "\x1b[31;1m"
    reset = "\x1b[0m"

    FORMATS = (
        {
            logging.DEBUG: green
            + f"{reset}[%(asctime)s]{green}[%(levelname)-7s][%(name)-14s]\
{reset}[{red}%(lineno)4s{reset}] %(message)s"
            + reset,
            logging.INFO: grey
            + f"{reset}[%(asctime)s]{grey}[%(levelname)-7s][%(name)-14s]\
{reset}[{red}%(lineno)4s{reset}] %(message)s"
            + reset,
            logging.WARNING: yellow
            + f"[%(asctime)s][%(levelname)-7s][%(name)-14s]\
[{red}%(lineno)4s{reset}{yellow}] %(message)s"
            + reset,
            logging.ERROR: red
            + "[%(asctime)s][%(levelname)-7s][%(name)-14s]\
[%(lineno)4s] %(message)s"
            + reset,
            logging.CRITICAL: bold_red
            + "[%(asctime)s][%(levelname)-7s][%(name)-14s][%(lineno)4s] %(message)s"
            + reset,
        }
        if DEBUG
        else {
            logging.DEBUG: reset,
            logging.INFO: grey + "[%(asctime)s][%(levelname)7s] %(message)s" + reset,
            logging.WARNING: yellow + "[%(asctime)s][%(levelname)7s] %(message)s" + reset,
            logging.ERROR: red + "[%(asctime)s][%(levelname)7s] %(message)s" + reset,
            logging.CRITICAL: bold_red + "[%(asctime)s][%(levelname)7s] %(message)s" + reset,
        }
    )

    def format(self, record: logging.LogRecord) -> str:
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt, datefmt="%Y-%m-%d %H:%M:%S")
        text = formatter.format(record)
        context = _context_of(record)
        if not context:
            return text
        suffix = " ".join(f"{_TEXT_KEYS.get(k, k)}={v}" for k, v in context.items())
        first, newline, rest = text.partition("\n")  # keep a traceback below the suffix
        return f"{first} [{suffix}]{newline}{rest}"


def make_formatter() -> logging.Formatter:
    """The formatter for console output, as chosen by ``MICHEL_LOG_FORMAT``."""
    return JsonFormatter() if JSON_LOGS else CustomFormatter()


_CONFIGURED_ATTR = "_michel_configured"


def _attach_webui_handler(lgr: logging.Logger) -> None:
    """Attach the WebUI log handler to a logger if it exists and isn't already attached."""
    try:
        from src.webui.log_handler import WebUILogHandler
    except ImportError:
        return
    webui = WebUILogHandler.get_instance()
    if webui is not None and webui not in lgr.handlers:
        lgr.addHandler(webui)


def _configure_logger(name: str) -> logging.Logger:
    """Configure (or return) a logger with a StreamHandler plus the WebUI handler.

    Uses ``logging.getLogger`` so every logger is part of the standard hierarchy —
    that lets the WebUI handler find and attach to all of them.
    ``propagate=False`` preserves the per-module handler layout so stdout output
    isn't duplicated via root.
    """
    lgr = logging.getLogger(name)
    if not getattr(lgr, _CONFIGURED_ATTR, False):
        # Keep the logger itself at DEBUG so the WebUI (which may want DEBUG)
        # always sees records; the StreamHandler does its own level filtering.
        lgr.setLevel(logging.DEBUG)
        lgr.propagate = False
        ch = logging.StreamHandler()
        ch.setLevel(logging.DEBUG if DEBUG else logging.INFO)
        ch.setFormatter(make_formatter())
        lgr.addHandler(ch)
        setattr(lgr, _CONFIGURED_ATTR, True)
    _attach_webui_handler(lgr)
    return lgr


def adopt_library_logger(name: str, level: int = logging.WARNING) -> logging.Logger:
    """Give a library's logger our handlers (console format, WebUI log stream).

    interactions.py logs through ``logging.getLogger("interactions")`` with no
    handler of its own, so its errors (a failing ``Task``…) otherwise only
    reach logging's last-resort handler: bare text, outside the JSON format and
    the dashboard. Console output starts at *level* to keep its routine
    chatter out.
    """
    lgr = _configure_logger(name)
    for handler in lgr.handlers:
        if isinstance(handler, logging.StreamHandler) and handler.formatter is not None:
            handler.setLevel(logging.DEBUG if DEBUG else level)
    return lgr


def get_logger(name: str) -> logging.Logger:
    """Return a configured logger by name.

    Useful for modules that have already initialized a logger, e.g. the
    interactions.py loggers we want to reformat.
    """
    return _configure_logger(name)


def init_logger(name: str = "root") -> logging.Logger:
    """Create a designated logger for separate modules."""
    return _configure_logger(name)


def log_failure(
    logger: logging.Logger, msg: str, *args: object, error: BaseException | None = None
) -> None:
    """Log a failure the code caught, at ERROR, with its traceback unless it is expected.

    Call it in an ``except`` block, where it takes the exception being handled,
    or pass *error*. An expected failure (:func:`src.core.errors.is_expected_failure`:
    a database or API outage, a refusal from Discord) stays one line; anything
    else is a bug and carries its traceback, which error reporting forwards
    (:mod:`src.core.error_reporting`). The record points at the caller.
    """
    if error is None:
        error = sys.exception()
    exc_info = error if error is not None and not is_expected_failure(error) else None
    logger.error(msg, *args, exc_info=exc_info, stacklevel=2)
