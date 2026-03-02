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
    Install the WebUI log handler on the root logger so it captures
    all log output from every module.
    """
    handler = WebUILogHandler(max_entries=max_entries)
    formatter = logging.Formatter(
        "[%(asctime)s][%(levelname)-7s][%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.addHandler(handler)

    # Also ensure root logger level is DEBUG so all records pass through
    if root_logger.level > logging.DEBUG:
        root_logger.setLevel(logging.DEBUG)

    return handler
