"""Simple TTL cache used by the XP extension for rank and member lookups."""

import time
from typing import Any


class TTLCache:
    """Thread-unsafe in-memory TTL cache — good enough for single-process bot use."""

    def __init__(self, ttl: int = 300):
        self._cache: dict[str, tuple[Any, float]] = {}
        self._ttl = ttl

    def get(self, key: str) -> Any | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        value, timestamp = entry
        if time.time() - timestamp < self._ttl:
            return value
        del self._cache[key]
        return None

    def set(self, key: str, value: Any) -> None:
        self._cache[key] = (value, time.time())

    def delete(self, key: str) -> None:
        self._cache.pop(key, None)

    def clear(self) -> None:
        self._cache.clear()

    def cleanup(self) -> None:
        """Drop all entries whose TTL has elapsed."""
        current_time = time.time()
        expired_keys = [
            key
            for key, (_, timestamp) in self._cache.items()
            if current_time - timestamp >= self._ttl
        ]
        for key in expired_keys:
            del self._cache[key]

    def keys_with_prefix(self, prefix: str) -> list[str]:
        """Snapshot of cache keys that start with ``prefix`` (useful for selective invalidation)."""
        return [key for key in list(self._cache.keys()) if key.startswith(prefix)]


__all__ = ["TTLCache"]
