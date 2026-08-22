import os
import asyncio
import logging
import aiosqlite
from typing import Any, List, Optional, Tuple

logger = logging.getLogger("PriestyAI.Database")

class Database:
    def __init__(self, db_path: str = "data/priesty.db"):
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        async with self._lock:
            if self._conn is not None:
                return

            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            self._conn = await aiosqlite.connect(self.db_path)
            self._conn.row_factory = aiosqlite.Row

            schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
            if os.path.exists(schema_path):
                with open(schema_path, "r", encoding="utf-8") as f:
                    schema_script = f.read()
                await self._conn.executescript(schema_script)
                await self._conn.commit()
                logger.info(f"Database initialized and verified at: {self.db_path}")
            else:
                logger.error(f"Schema file not found at: {schema_path}")

    async def _ensure_connected(self) -> None:
        if self._conn is None:
            await self.connect()

    async def close(self) -> None:
        async with self._lock:
            if self._conn:
                await self._conn.close()
                self._conn = None
                logger.info("Database connection closed cleanly.")

    async def execute(self, query: str, parameters: Tuple[Any, ...] = ()) -> aiosqlite.Cursor:
        await self._ensure_connected()
        assert self._conn is not None
        cursor = await self._conn.execute(query, parameters)
        await self._conn.commit()
        return cursor

    async def executemany(self, query: str, parameters: List[Tuple[Any, ...]]) -> aiosqlite.Cursor:
        await self._ensure_connected()
        assert self._conn is not None
        cursor = await self._conn.executemany(query, parameters)
        await self._conn.commit()
        return cursor

    async def fetch_one(self, query: str, parameters: Tuple[Any, ...] = ()) -> Optional[aiosqlite.Row]:
        await self._ensure_connected()
        assert self._conn is not None
        async with self._conn.execute(query, parameters) as cursor:
            return await cursor.fetchone()

    async def fetch_all(self, query: str, parameters: Tuple[Any, ...] = ()) -> List[aiosqlite.Row]:
        await self._ensure_connected()
        assert self._conn is not None
        async with self._conn.execute(query, parameters) as cursor:
            return await cursor.fetchall()

db = Database()