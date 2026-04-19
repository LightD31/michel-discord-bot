"""Colored ANSI logger factory.

Moved from ``src/logutil.py`` as part of the ``src/core/`` restructure. The old
module path stays available as a re-export shim.
"""

import logging
import os

# DEBUG flag, sourced from the ``MICHEL_DEBUG`` environment variable.
# Phase 5: replaces the standalone ``config.py`` flag at the repo root.
DEBUG = os.environ.get("MICHEL_DEBUG", "").lower() in ("1", "true", "yes", "on")


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
        return formatter.format(record)


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
        ch.setFormatter(CustomFormatter())
        lgr.addHandler(ch)
        setattr(lgr, _CONFIGURED_ATTR, True)
    _attach_webui_handler(lgr)
    return lgr


def overwrite_ipy_loggers() -> None:
    targets = {"mixin", "dispatch", "http", "gateway", "client", "context"}
    for k, v in logging.Logger.manager.loggerDict.items():
        if k in targets and isinstance(v, logging.Logger):
            for h in v.handlers:
                h.setFormatter(CustomFormatter())


def get_logger(name: str) -> logging.Logger:
    """Return a configured logger by name.

    Useful for modules that have already initialized a logger, e.g. the
    interactions.py loggers we want to reformat.
    """
    return _configure_logger(name)


def init_logger(name: str = "root") -> logging.Logger:
    """Create a designated logger for separate modules."""
    return _configure_logger(name)
