"""
In-memory log handler for the Web UI log visualizer.
Captures log records into a ring buffer and supports SSE streaming.
"""

import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from typing import Optional


@dataclass
class LogEntry:
    """Represents a single log entry."""
    timestamp: float
    level: str
    logger_name: str
    message: str
    lineno: int
    filename: str

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "level": self.level,
            "logger": self.logger_name,
            "message": self.message,
            "lineno": self.lineno,
            "filename": self.filename,
        }


class WebUILogHandler(logging.Handler):
    """
    A logging handler that captures records into a fixed-size ring buffer
    and notifies any SSE listeners.
    """

    _instance: Optional["WebUILogHandler"] = None

    def __init__(self, max_entries: int = 2000):
        super().__init__()
        self.buffer: deque[LogEntry] = deque(maxlen=max_entries)
        self._listeners: list[asyncio.Queue] = []
        self.setLevel(logging.DEBUG)
        WebUILogHandler._instance = self

    @classmethod
    def get_instance(cls) -> Optional["WebUILogHandler"]:
        return cls._instance

    def emit(self, record: logging.LogRecord):
        try:
            entry = LogEntry(
                timestamp=record.created,
                level=record.levelname,
                logger_name=record.name,
                message=self.format(record) if self.formatter else record.getMessage(),
                lineno=record.lineno,
                filename=record.filename,
            )
            self.buffer.append(entry)
            # Notify all SSE listeners (non-blocking)
            for queue in self._listeners:
                try:
                    queue.put_nowait(entry)
                except asyncio.QueueFull:
                    pass  # Drop if consumer is too slow
        except Exception:
            self.handleError(record)

    def get_recent(self, count: int = 200, level: Optional[str] = None,
                   search: Optional[str] = None, logger_name: Optional[str] = None) -> list[dict]:
        """Get recent log entries with optional filtering."""
        entries = list(self.buffer)

        if level:
            level_upper = level.upper()
            level_num = getattr(logging, level_upper, None)
            if level_num is not None:
                entries = [e for e in entries if getattr(logging, e.level, 0) >= level_num]

        if logger_name:
            entries = [e for e in entries if logger_name.lower() in e.logger_name.lower()]

        if search:
            search_lower = search.lower()
            entries = [e for e in entries if search_lower in e.message.lower()
                       or search_lower in e.logger_name.lower()]

        return [e.to_dict() for e in entries[-count:]]

    def subscribe(self) -> asyncio.Queue:
        """Create a new SSE listener queue."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._listeners.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue):
        """Remove an SSE listener queue."""
        try:
            self._listeners.remove(queue)
        except ValueError:
            pass


def install_log_handler(max_entries: int = 2000) -> WebUILogHandler:
    """
    Install the WebUI log handler on the root logger AND on all existing
    loggers (including standalone ones created via logging.Logger() directly).

    Also monkey-patches logutil.init_logger and logutil.get_logger so any
    logger created *after* this call automatically gets the WebUI handler.
    """
    handler = WebUILogHandler(max_entries=max_entries)
    formatter = logging.Formatter(
        "[%(asctime)s][%(levelname)-7s][%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)

    # 1) Attach to root logger (captures standard-hierarchy loggers)
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    if root_logger.level > logging.DEBUG:
        root_logger.setLevel(logging.DEBUG)

    # 2) Attach to ALL existing loggers in the manager dict
    #    This catches loggers created via logging.getLogger(name)
    for name, logger_ref in logging.Logger.manager.loggerDict.items():
        if isinstance(logger_ref, logging.Logger):
            if handler not in logger_ref.handlers:
                logger_ref.addHandler(handler)

    # 3) Attach to standalone loggers created via logging.Logger() directly.
    #    These aren't in the manager dict, so we monkey-patch logutil to
    #    inject our handler into every future logger it creates.
    try:
        from src import logutil as _logutil

        _orig_init_logger = _logutil.init_logger
        _orig_get_logger = _logutil.get_logger

        def _patched_init_logger(name="root"):
            lgr = _orig_init_logger(name)
            if handler not in lgr.handlers:
                lgr.addHandler(handler)
            return lgr

        def _patched_get_logger(name):
            lgr = _orig_get_logger(name)
            if handler not in lgr.handlers:
                lgr.addHandler(handler)
            return lgr

        _logutil.init_logger = _patched_init_logger
        _logutil.get_logger = _patched_get_logger

        # 4) Retroactively attach to loggers that were already created
        #    by scanning all live objects is impractical, but we can scan
        #    the gc for Logger instances that aren't in the manager dict.
        import gc
        for obj in gc.get_objects():
            if isinstance(obj, logging.Logger) and handler not in obj.handlers:
                obj.addHandler(handler)

    except ImportError:
        pass

    return handler
