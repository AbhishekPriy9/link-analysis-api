import time
from typing import Any


class TtlCache:
    def __init__(self, ttl_seconds: int, max_entries: int = 256) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_entries = max(1, max_entries)
        self._entries: dict[str, tuple[float, dict[str, Any]]] = {}

    def get(self, key: str) -> dict[str, Any] | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() >= expires_at:
            del self._entries[key]
            return None
        return value

    def set(self, key: str, value: dict[str, Any]) -> None:
        if key not in self._entries and len(self._entries) >= self._max_entries:
            self._evict()
        self._entries[key] = (time.monotonic() + self._ttl_seconds, value)

    def _evict(self) -> None:
        now = time.monotonic()
        expired = [key for key, (expires, _) in self._entries.items() if now >= expires]
        for key in expired:
            del self._entries[key]

        if len(self._entries) >= self._max_entries:
            oldest = min(self._entries, key=lambda key: self._entries[key][0])
            del self._entries[oldest]

    def clear(self) -> None:
        self._entries.clear()
