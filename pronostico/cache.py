from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable


class TTLCache:
    """Cache simple en memoria con TTL y proteccion contra peticiones duplicadas."""

    def __init__(self, max_items: int = 400) -> None:
        self._data: dict[str, tuple[float, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._max_items = max_items

    def get(self, key: str) -> Any | None:
        item = self._data.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at <= time.monotonic():
            self._data.pop(key, None)
            return None
        return value

    async def get_or_set(
        self,
        key: str,
        ttl: float,
        factory: Callable[[], Awaitable[Any]],
    ) -> Any:
        value = self.get(key)
        if value is not None:
            return value

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            value = self.get(key)
            if value is not None:
                return value
            value = await factory()
            self._data[key] = (time.monotonic() + ttl, value)
            self._cleanup()
            return value

    def _cleanup(self) -> None:
        if len(self._data) <= self._max_items:
            return
        now = time.monotonic()
        expired = [key for key, (expires_at, _) in self._data.items() if expires_at <= now]
        for key in expired:
            self._data.pop(key, None)
            self._locks.pop(key, None)
        if len(self._data) > self._max_items:
            for key in list(self._data)[: len(self._data) - self._max_items]:
                self._data.pop(key, None)
                self._locks.pop(key, None)


cache = TTLCache()
