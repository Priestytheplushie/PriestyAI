import os
import time
import json
import struct
import math
import sqlite3
import asyncio
import logging
from typing import Any
from google.genai import types
from core.client_manager import client_manager

logger = logging.getLogger("PriestyAI.Memory")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "priestyai.db")

EMBEDDING_MODELS = ["gemini-embedding-001", "gemini-embedding-2"]
OUTPUT_DIMENSIONALITY = 768

def pack_vector(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)

def unpack_vector(blob: bytes) -> list[float]:
    count = len(blob) // 4
    return list(struct.unpack(f"{count}f", blob))

def cosine_similarity(v1: list[float], v2: list[float]) -> float:
    if len(v1) != len(v2) or not v1:
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    if norm1 == 0.0 or norm2 == 0.0:
        return 0.0
    return dot / (norm1 * norm2)

class MemoryManager:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL CHECK(category IN ('user', 'server')),
                    entity_id TEXT NOT NULL,
                    memory_text TEXT NOT NULL,
                    embedding BLOB NOT NULL,
                    importance_score REAL DEFAULT 0.7,
                    access_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_accessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_memories_entity ON memories(category, entity_id)")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    session_id TEXT PRIMARY KEY,
                    channel_id TEXT NOT NULL,
                    guild_id TEXT,
                    creator_user_id TEXT NOT NULL,
                    history_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_active_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_channel ON chat_sessions(channel_id)")

            conn.commit()
        logger.info(f"Initialized SQLite Memory & Session Database at '{self.db_path}'.")

    async def generate_embedding(self, text: str) -> list[float] | None:
        clean_text = text.strip()[:1000]
        if not clean_text:
            return None

        for model_name in EMBEDDING_MODELS:
            for attempt in range(client_manager.key_count):
                client, key_idx, _ = client_manager.get_client_for_model("gemini-3.5-flash-lite")
                try:
                    config = types.EmbedContentConfig(output_dimensionality=OUTPUT_DIMENSIONALITY)
                    response = await client.aio.models.embed_content(
                        model=model_name,
                        contents=clean_text,
                        config=config
                    )
                    if getattr(response, "embeddings", None) and response.embeddings:
                        return response.embeddings[0].values
                    elif getattr(response, "embedding", None) and getattr(response.embedding, "values", None):
                        return response.embedding.values
                except Exception as e:
                    client_manager.report_error(key_idx, model_name, e)
                    logger.warning(f"Embedding attempt failed on '{model_name}' (Key #{key_idx}): {e}")

        logger.error("All API keys failed to generate embedding vector.")
        return None

    async def remember(
        self,
        category: str,
        entity_id: str,
        memory_text: str,
        importance: float = 0.7
    ) -> dict[str, Any]:
        cat_clean = category.strip().lower()
        if cat_clean not in ["user", "server"]:
            return {"error": "Category must be either 'user' or 'server'."}

        vec = await self.generate_embedding(memory_text)
        if not vec:
            return {"error": "Failed to generate vector embedding for memory."}

        packed_vec = pack_vector(vec)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, memory_text, embedding FROM memories WHERE category = ? AND entity_id = ?",
                (cat_clean, str(entity_id))
            )
            rows = cursor.fetchall()

            for row in rows:
                existing_vec = unpack_vector(row["embedding"])
                sim = cosine_similarity(vec, existing_vec)
                if sim >= 0.88:
                    mem_id = row["id"]
                    cursor.execute("""
                        UPDATE memories
                        SET memory_text = ?, embedding = ?, importance_score = ?, last_accessed_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (memory_text, packed_vec, importance, mem_id))
                    conn.commit()
                    logger.info(f"[Memory Updated] Refined memory ID #{mem_id} (similarity: {sim:.2f}): '{memory_text}'")
                    return {
                        "status": "updated",
                        "memory_id": mem_id,
                        "category": cat_clean,
                        "message": f"Updated and refined existing memory #{mem_id}."
                    }

            cursor.execute("""
                INSERT INTO memories (category, entity_id, memory_text, embedding, importance_score)
                VALUES (?, ?, ?, ?, ?)
            """, (cat_clean, str(entity_id), memory_text, packed_vec, importance))
            conn.commit()
            new_id = cursor.lastrowid

        logger.info(f"[Memory Stored] Saved new {cat_clean} memory #{new_id}: '{memory_text}'")
        return {
            "status": "saved",
            "memory_id": new_id,
            "category": cat_clean,
            "message": f"Successfully remembered into {cat_clean} storage."
        }

    def get_memory_by_id(self, memory_id: int) -> dict[str, Any] | None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM memories WHERE id = ?", (memory_id,))
            row = cursor.fetchone()
            if row:
                return dict(row)
        return None

    def get_all_memories_for_entity(self, category: str, entity_id: str | int) -> list[dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM memories WHERE category = ? AND entity_id = ? ORDER BY created_at DESC",
                (category.lower().strip(), str(entity_id))
            )
            return [dict(row) for row in cursor.fetchall()]

    async def update_memory_text(self, memory_id: int, new_text: str) -> bool:
        clean_text = new_text.strip()
        if not clean_text:
            return False

        vec = await self.generate_embedding(clean_text)
        if not vec:
            return False

        packed_vec = pack_vector(vec)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE memories
                SET memory_text = ?, embedding = ?, last_accessed_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (clean_text, packed_vec, memory_id))
            conn.commit()
            return cursor.rowcount > 0

    async def forget(self, memory_id: int, reason: str = "") -> dict[str, Any]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, memory_text FROM memories WHERE id = ?", (memory_id,))
            row = cursor.fetchone()
            if not row:
                return {"error": f"Memory ID #{memory_id} not found."}

            deleted_text = row["memory_text"]
            cursor.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            conn.commit()

        logger.info(f"[Memory Forgotten] Deleted memory #{memory_id}: '{deleted_text}' (Reason: {reason})")
        return {
            "status": "forgotten",
            "memory_id": memory_id,
            "deleted_text": deleted_text,
            "message": f"Memory #{memory_id} was removed from memory banks."
        }

    def delete_all_user_memories(self, user_id: str | int) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM memories WHERE category = 'user' AND entity_id = ?", (str(user_id),))
            conn.commit()
            return cursor.rowcount

    def delete_all_server_lore(self, guild_id: str | int) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM memories WHERE category = 'server' AND entity_id = ?", (str(guild_id),))
            conn.commit()
            return cursor.rowcount

    def purge_entire_user_data(self, user_id: str | int) -> dict[str, int]:
        uid_str = str(user_id)
        counts = {}
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM memories WHERE category = 'user' AND entity_id = ?", (uid_str,))
            counts["memories"] = cursor.rowcount

            cursor.execute("DELETE FROM chat_sessions WHERE creator_user_id = ?", (uid_str,))
            counts["chat_sessions"] = cursor.rowcount

            cursor.execute("DELETE FROM user_configs WHERE user_id = ?", (uid_str,))
            counts["user_configs"] = cursor.rowcount

            cursor.execute("DELETE FROM message_generations WHERE author_id = ?", (uid_str,))
            counts["message_generations"] = cursor.rowcount

            conn.commit()
        logger.info(f"[Complete User Data Purge] User ID {user_id}: {counts}")
        return counts

    async def recall_relevant_memories(
        self,
        query: str,
        user_id: int | str,
        guild_id: int | str | None,
        top_k: int = 3
    ) -> dict[str, list[dict[str, Any]]]:
        query_vec = await self.generate_embedding(query)
        if not query_vec:
            return {"user_memories": [], "server_lore": []}

        user_id_str = str(user_id)
        guild_id_str = str(guild_id) if guild_id else None

        user_candidates = []
        server_candidates = []

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, memory_text, embedding, importance_score FROM memories WHERE category = 'user' AND entity_id = ?",
                (user_id_str,)
            )
            for row in cursor.fetchall():
                vec = unpack_vector(row["embedding"])
                sim = cosine_similarity(query_vec, vec)
                if sim >= 0.40:
                    user_candidates.append({
                        "id": row["id"],
                        "text": row["memory_text"],
                        "similarity": sim,
                        "importance": row["importance_score"]
                    })

            if guild_id_str:
                cursor.execute(
                    "SELECT id, memory_text, embedding, importance_score FROM memories WHERE category = 'server' AND entity_id = ?",
                    (guild_id_str,)
                )
                for row in cursor.fetchall():
                    vec = unpack_vector(row["embedding"])
                    sim = cosine_similarity(query_vec, vec)
                    if sim >= 0.40:
                        server_candidates.append({
                            "id": row["id"],
                            "text": row["memory_text"],
                            "similarity": sim,
                            "importance": row["importance_score"]
                        })

            all_retrieved_ids = [m["id"] for m in user_candidates + server_candidates]
            if all_retrieved_ids:
                placeholders = ",".join("?" * len(all_retrieved_ids))
                cursor.execute(f"""
                    UPDATE memories 
                    SET access_count = access_count + 1, last_accessed_at = CURRENT_TIMESTAMP
                    WHERE id IN ({placeholders})
                """, all_retrieved_ids)
                conn.commit()

        user_candidates.sort(key=lambda x: x["similarity"] * x["importance"], reverse=True)
        server_candidates.sort(key=lambda x: x["similarity"] * x["importance"], reverse=True)

        return {
            "user_memories": user_candidates[:top_k],
            "server_lore": server_candidates[:top_k]
        }

    def save_chat_session(self, session_id: str, channel_id: str, guild_id: str | None, user_id: str, history: list[dict[str, str]]):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO chat_sessions (session_id, channel_id, guild_id, creator_user_id, history_json, last_active_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(session_id) DO UPDATE SET
                    history_json = excluded.history_json,
                    last_active_at = CURRENT_TIMESTAMP
            """, (session_id, str(channel_id), str(guild_id) if guild_id else None, str(user_id), json.dumps(history)))
            conn.commit()

    def get_chat_session(self, session_id: str) -> list[dict[str, str]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT history_json FROM chat_sessions WHERE session_id = ?", (session_id,))
            row = cursor.fetchone()
            if row:
                try:
                    return json.loads(row["history_json"])
                except Exception:
                    pass
        return []

memory_manager = MemoryManager()