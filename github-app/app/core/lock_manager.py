import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Dict


class KeyedLockManager:
    """
    Manages per-resource asyncio locks keyed by identifier (e.g. 'owner/repo#123').
    Prevents race conditions and git reference conflicts on concurrent webhook deliveries.
    """

    def __init__(self) -> None:
        self._locks: Dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    @asynccontextmanager
    async def lock(self, key: str) -> AsyncGenerator[None, None]:
        async with self._global_lock:
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
            target_lock = self._locks[key]

        async with target_lock:
            yield


lock_manager = KeyedLockManager()
