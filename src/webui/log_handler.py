"""
In-memory log handler for the Web UI log visualizer.
Captures log records into a ring buffer and supports SSE streaming.
"""

import asyncio
import contextlib
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

    # Loggers to ignore to prevent feedback loops (SSE logging its own
    # chunks) and reduce noise from infrastructure loggers.
    _IGNORED_LOGGERS = frozenset(
        {
            "sse_starlette",
            "sse_starlette.sse",
            "uvicorn.access",
        }
    )

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
        # Skip loggers that would cause infinite feedback loops
        if record.name in self._IGNORED_LOGGERS:
            return
        try:
            entry = LogEntry(
                timestamp=record.created,
                level=record.levelname,
                logger_name=record.name,
                # Store only the raw message — the UI renders its own columns
                # (time/level/logger/lineno) so any prefix here would duplicate them.
                message=record.getMessage(),
                lineno=record.lineno,
                filename=record.filename,
            )
            self.buffer.append(entry)
            # Notify all SSE listeners (non-blocking), prune dead queues
            dead = []
            for queue in self._listeners:
                try:
                    queue.put_nowait(entry)
                except asyncio.QueueFull:
                    # Queue is full — consumer is too slow or dead.
                    # Drain the oldest entry to make room and retry once.
                    try:
                        queue.get_nowait()
                        queue.put_nowait(entry)
                    except Exception:
                        dead.append(queue)
                except Exception:
                    dead.append(queue)
            # Remove dead queues
            for q in dead:
                with contextlib.suppress(ValueError):
                    self._listeners.remove(q)
        except Exception:
            self.handleError(record)

    def get_recent(
        self,
        count: int = 200,
        level: str | None = None,
        search: str | None = None,
        logger_name: str | None = None,
    ) -> list[dict]:
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
            entries = [
                e
                for e in entries
                if search_lower in e.message.lower() or search_lower in e.logger_name.lower()
            ]

        return [e.to_dict() for e in entries[-count:]]

    def subscribe(self) -> asyncio.Queue:
        """Create a new SSE listener queue."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._listeners.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue):
        """Remove an SSE listener queue."""
        with contextlib.suppress(ValueError):
            self._listeners.remove(queue)
        # Drain the queue to release any held LogEntry references
        while not queue.empty():
            try:
                queue.get_nowait()
            except Exception:
                break

    @property
    def listener_count(self) -> int:
        """Number of active SSE listeners (useful for diagnostics)."""
        return len(self._listeners)


def install_log_handler(max_entries: int = 2000) -> WebUILogHandler:
    """
    Install the WebUI log handler on every logger configured by logutil.

    Since logutil loggers have propagate=False, the handler must be attached to each
    one directly. New loggers created later via logutil.init_logger/get_logger will
    auto-attach because _configure_logger calls _attach_webui_handler.
    """
    handler = WebUILogHandler(max_entries=max_entries)

    for logger_ref in logging.Logger.manager.loggerDict.values():
        if isinstance(logger_ref, logging.Logger) and handler not in logger_ref.handlers:
            logger_ref.addHandler(handler)

    return handler
