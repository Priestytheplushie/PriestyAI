import time
import logging
from typing import List, Dict, Optional, Any
from src.database.db import db

logger = logging.getLogger("PriestyAI.MemoryManager")

class MemoryManager:


    async def save_message(
        self,
        message_id: str,
        channel_id: str,
        guild_id: Optional[str],
        author_id: str,
        author_name: str,
        content: str,
        role: str
    ) -> None:
        now = int(time.time())
        query = """
            INSERT INTO channel_messages 
            (message_id, channel_id, guild_id, author_id, author_name, content, role, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        await db.execute(
            query,
            (str(message_id), str(channel_id), str(guild_id) if guild_id else None,
             str(author_id), author_name, content, role, now)
        )

    async def get_channel_history(self, channel_id: str, limit: int = 15) -> List[Dict[str, Any]]:
        query = """
            SELECT message_id, channel_id, author_id, author_name, content, role, created_at
            FROM channel_messages
            WHERE channel_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """
        rows = await db.fetch_all(query, (str(channel_id), limit))
        history = [dict(row) for row in reversed(rows)]
        return history

    async def clear_channel_history(self, channel_id: str) -> int:
        query = "DELETE FROM channel_messages WHERE channel_id = ?"
        cursor = await db.execute(query, (str(channel_id),))
        logger.info(f"Cleared {cursor.rowcount} messages from channel memory buffer for: {channel_id}")
        return cursor.rowcount


    async def add_user_memory(self, user_id: str, fact: str, category: str = "general") -> None:
        now = int(time.time())
        query = """
            INSERT INTO user_memories (user_id, fact, category, created_at, last_accessed_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, fact) DO UPDATE SET
                last_accessed_at = excluded.last_accessed_at,
                category = excluded.category
        """
        await db.execute(query, (str(user_id), fact.strip(), category, now, now))
        logger.info(f"Updated memory for user {user_id}: {fact[:50]}...")

    async def get_user_memories(self, user_id: str, limit: int = 6) -> List[str]:
        now = int(time.time())
        query = """
            SELECT fact FROM user_memories
            WHERE user_id = ?
            ORDER BY last_accessed_at DESC
            LIMIT ?
        """
        rows = await db.fetch_all(query, (str(user_id), limit))
        
        if rows:
            touch_query = "UPDATE user_memories SET last_accessed_at = ? WHERE user_id = ?"
            await db.execute(touch_query, (now, str(user_id)))

        return [row["fact"] for row in rows]


    async def add_server_lore(
        self,
        guild_id: str,
        topic: str,
        content: str,
        confidence: float = 1.0
    ) -> None:
        now = int(time.time())
        query = """
            INSERT INTO server_lore (guild_id, topic, content, confidence, last_observed_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, topic, content) DO UPDATE SET
                confidence = MIN(1.0, server_lore.confidence + 0.1),
                last_observed_at = excluded.last_observed_at
        """
        await db.execute(query, (str(guild_id), topic.strip(), content.strip(), confidence, now))

    async def get_server_lore(self, guild_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        query = """
            SELECT topic, content, confidence
            FROM server_lore
            WHERE guild_id = ?
            ORDER BY confidence DESC, last_observed_at DESC
            LIMIT ?
        """
        rows = await db.fetch_all(query, (str(guild_id), limit))
        return [dict(row) for row in rows]


    async def update_server_vibe(self, guild_id: str, vibe_summary: str) -> None:
        now = int(time.time())
        query = """
            INSERT INTO server_vibes (guild_id, vibe_summary, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                vibe_summary = excluded.vibe_summary,
                updated_at = excluded.updated_at
        """
        await db.execute(query, (str(guild_id), vibe_summary.strip(), now))
        logger.info(f"Updated dynamic vibe profile for Guild: {guild_id}")

    async def get_server_vibe(self, guild_id: str) -> Optional[str]:
        query = "SELECT vibe_summary FROM server_vibes WHERE guild_id = ?"
        row = await db.fetch_one(query, (str(guild_id),))
        return row["vibe_summary"] if row else None


    async def add_watched_channel(
        self,
        channel_id: str,
        guild_id: str,
        duration_minutes: int = 30,
        reason: Optional[str] = None
    ) -> int:
        now = int(time.time())
        watch_until = now + (duration_minutes * 60)
        query = """
            INSERT INTO watched_channels (channel_id, guild_id, watch_until, reason, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(channel_id) DO UPDATE SET
                watch_until = excluded.watch_until,
                reason = excluded.reason
        """
        await db.execute(query, (str(channel_id), str(guild_id), watch_until, reason, now))
        return watch_until

    async def remove_watched_channel(self, channel_id: str) -> None:
        query = "DELETE FROM watched_channels WHERE channel_id = ?"
        await db.execute(query, (str(channel_id),))

    async def get_active_watched_channels(self) -> List[Dict[str, Any]]:
        now = int(time.time())
        await db.execute("DELETE FROM watched_channels WHERE watch_until < ?", (now,))
        rows = await db.fetch_all("SELECT channel_id, guild_id, watch_until, reason FROM watched_channels")
        return [dict(row) for row in rows]

memory_manager = MemoryManager()