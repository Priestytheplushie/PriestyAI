import os
import time
import json
import struct
import math
import sqlite3
import asyncio
import logging
from typing import Any
from pydantic import BaseModel, Field
from google.genai import types
from core.client_manager import client_manager
from core.encryption import encryption_manager

logger = logging.getLogger("PriestyAI.Memory")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "priestyai.db")

EMBEDDING_MODELS = ["gemini-embedding-001", "gemini-embedding-2"]
OUTPUT_DIMENSIONALITY = 768

def get_user_chat_session_id(channel_id: str | int | None, user_id: str | int) -> str:
    chan_str = str(channel_id) if channel_id else "dm"
    return f"chat_{chan_str}_{user_id}"

class MemoryExtractionSchema(BaseModel):
    has_memories: bool = Field(description="True if durable personal facts or server lore should be saved, False if query is generic")
    user_facts: list[str] = Field(default_factory=list, description="Clear, concise personal facts about the user. Empty if none.")
    server_lore: list[str] = Field(default_factory=list, description="Durable server project facts or guild lore. Empty if none.")

MEMORY_EXTRACTOR_INSTRUCTION = """You are the background long-term memory extractor for PriestyAI.
Analyze the user's prompt and extract any durable, high-value facts worth remembering.

WHAT TO SAVE:
- User Identity / Background / Experience (e.g. "I'm a senior frontend dev", "My name is Sam")
- Tech Stack / Languages / Preferences (e.g. "I use Arch Linux", "I write Rust and TypeScript", "I prefer concise code without comments")
- Persistent Project Lore (e.g. "We are developing a 2D RPG called Aether", "Our server bot uses SQLite")

WHAT TO IGNORE:
- Fleeting questions, one-off debugging requests, casual banter, greetings, or temporary roleplay.

Output a strict JSON adhering to the schema.
"""

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
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute("PRAGMA foreign_keys = ON;")
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

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS staged_chat_context (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    author_name TEXT NOT NULL,
                    author_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_staged_ctx ON staged_chat_context(channel_id, user_id)")

            conn.commit()

    async def generate_embedding(self, text: str) -> list[float] | None:
        clean_text = text.strip()[:1000]
        if not clean_text:
            return None

        for model_name in EMBEDDING_MODELS:
            for _ in range(client_manager.key_count):
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
        encrypted_text = encryption_manager.encrypt_text(memory_text.strip())

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
                    """, (encrypted_text, packed_vec, importance, mem_id))
                    conn.commit()
                    return {
                        "status": "updated",
                        "memory_id": mem_id,
                        "category": cat_clean,
                        "message": f"Updated existing memory #{mem_id}."
                    }

            cursor.execute("""
                INSERT INTO memories (category, entity_id, memory_text, embedding, importance_score)
                VALUES (?, ?, ?, ?, ?)
            """, (cat_clean, str(entity_id), encrypted_text, packed_vec, importance))
            conn.commit()
            new_id = cursor.lastrowid

        return {
            "status": "saved",
            "memory_id": new_id,
            "category": cat_clean,
            "message": f"Successfully remembered into {cat_clean} storage."
        }

    async def auto_extract_and_store_async(
        self,
        user_id: int | str,
        guild_id: int | str | None,
        prompt_text: str,
        user_memory_policy: str = "read_write",
        server_lore_policy: str = "read_write"
    ):
        if user_memory_policy != "read_write" and server_lore_policy != "read_write":
            return

        if len(prompt_text.strip()) < 15:
            return

        client, key_idx, active_model = client_manager.get_client_for_model("gemini-3.5-flash-lite")
        if not client:
            return

        try:
            config = types.GenerateContentConfig(
                system_instruction=MEMORY_EXTRACTOR_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=MemoryExtractionSchema,
                temperature=0.0
            )

            res = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=active_model,
                    contents=f"User Query:\n{prompt_text.strip()[:2000]}",
                    config=config
                ),
                timeout=2.5
            )

            if res.text:
                data = json.loads(res.text)
                extraction = MemoryExtractionSchema(**data)

                if extraction.has_memories:
                    if user_memory_policy == "read_write" and extraction.user_facts:
                        for fact in extraction.user_facts:
                            await self.remember(category="user", entity_id=str(user_id), memory_text=fact, importance=0.8)

                    if server_lore_policy == "read_write" and guild_id and extraction.server_lore:
                        for lore in extraction.server_lore:
                            await self.remember(category="server", entity_id=str(guild_id), memory_text=lore, importance=0.7)

        except Exception as e:
            logger.debug(f"Background auto-memory extraction skipped: {e}")

    def get_memory_by_id(self, memory_id: int) -> dict[str, Any] | None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM memories WHERE id = ?", (memory_id,))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["memory_text"] = encryption_manager.decrypt_text(d.get("memory_text", ""))
                return d
        return None

    def get_all_memories_for_entity(self, category: str, entity_id: str | int) -> list[dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM memories WHERE category = ? AND entity_id = ? ORDER BY created_at DESC",
                (category.lower().strip(), str(entity_id))
            )
            decrypted_rows = []
            for row in cursor.fetchall():
                d = dict(row)
                d["memory_text"] = encryption_manager.decrypt_text(d.get("memory_text", ""))
                decrypted_rows.append(d)
            return decrypted_rows

    async def update_memory_text(self, memory_id: int, new_text: str) -> bool:
        clean_text = new_text.strip()
        if not clean_text:
            return False

        vec = await self.generate_embedding(clean_text)
        if not vec:
            return False

        packed_vec = pack_vector(vec)
        encrypted_text = encryption_manager.encrypt_text(clean_text)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE memories
                SET memory_text = ?, embedding = ?, last_accessed_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (encrypted_text, packed_vec, memory_id))
            conn.commit()
            return cursor.rowcount > 0

    async def forget(self, memory_id: int, reason: str = "") -> dict[str, Any]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, memory_text FROM memories WHERE id = ?", (memory_id,))
            row = cursor.fetchone()
            if not row:
                return {"error": f"Memory ID #{memory_id} not found."}

            raw_text = row["memory_text"]
            deleted_text = encryption_manager.decrypt_text(raw_text)
            cursor.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            conn.commit()

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

    def delete_all_user_schedules(self, user_id: str | int) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM scheduled_tasks WHERE user_id = ?", (str(user_id),))
            conn.commit()
            return cursor.rowcount

    def export_user_data_bundle(self, user_id: str | int) -> dict[str, Any]:
        from core.config_manager import config_manager
        from core.custom_tool_manager import custom_tool_manager
        uid_str = str(user_id)
        u_cfg = config_manager.get_user_config(uid_str)
        mems = self.get_all_memories_for_entity("user", uid_str)
        user_tools = custom_tool_manager.get_tools_for_entity("user", uid_str)

        sessions = []
        schedules = []

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT session_id, channel_id, guild_id, history_json, created_at, last_active_at
                FROM chat_sessions
                WHERE creator_user_id = ?
            """, (uid_str,))
            for row in cursor.fetchall():
                try:
                    decrypted = encryption_manager.decrypt_text(row["history_json"])
                    sessions.append({
                        "session_id": row["session_id"],
                        "channel_id": row["channel_id"],
                        "guild_id": row["guild_id"],
                        "history": json.loads(decrypted),
                        "created_at": row["created_at"],
                        "last_active_at": row["last_active_at"]
                    })
                except Exception:
                    pass

            cursor.execute("""
                SELECT task_id, guild_id, channel_id, scope, prompt_text,
                       time_expression, summary_schedule, next_run_timestamp,
                       interval_type, interval_seconds, dm_delivery, is_active, created_at
                FROM scheduled_tasks
                WHERE user_id = ?
            """, (uid_str,))
            for s_row in cursor.fetchall():
                schedules.append(dict(s_row))

        return {
            "user_id": uid_str,
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "user_config": u_cfg,
            "custom_tools": user_tools,
            "memories": [
                {
                    "id": m["id"],
                    "memory_text": m["memory_text"],
                    "importance_score": m.get("importance_score", 0.7),
                    "access_count": m.get("access_count", 0),
                    "created_at": m.get("created_at", "")
                }
                for m in mems
            ],
            "scheduled_tasks": schedules,
            "chat_sessions": sessions
        }

    def purge_entire_user_data(self, user_id: str | int) -> dict[str, int]:
        from core.config_manager import config_manager
        from core.branch_manager import branch_manager
        uid_str = str(user_id)
        counts = {}
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM memories WHERE category = 'user' AND entity_id = ?", (uid_str,))
            counts["memories"] = cursor.rowcount

            cursor.execute("DELETE FROM chat_sessions WHERE creator_user_id = ?", (uid_str,))
            counts["chat_sessions"] = cursor.rowcount

            cursor.execute("DELETE FROM staged_chat_context WHERE user_id = ?", (uid_str,))
            counts["staged_chat_context"] = cursor.rowcount

            cursor.execute("DELETE FROM scheduled_tasks WHERE user_id = ?", (uid_str,))
            counts["scheduled_tasks"] = cursor.rowcount

            cursor.execute("DELETE FROM user_configs WHERE user_id = ?", (uid_str,))
            counts["user_configs"] = cursor.rowcount

            cursor.execute("DELETE FROM custom_tools WHERE scope = 'user' AND entity_id = ?", (uid_str,))
            counts["custom_tools"] = cursor.rowcount

            cursor.execute("DELETE FROM message_generations WHERE author_id = ?", (uid_str,))
            counts["message_generations"] = cursor.rowcount

            conn.commit()

        counts["branch_messages"] = branch_manager.purge_user_from_branches(uid_str)
        logger.info(f"[UserDataPurge] Purged data for user {user_id}: {counts}")
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
                    decrypted_text = encryption_manager.decrypt_text(row["memory_text"])
                    user_candidates.append({
                        "id": row["id"],
                        "text": decrypted_text,
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
                        decrypted_text = encryption_manager.decrypt_text(row["memory_text"])
                        server_candidates.append({
                            "id": row["id"],
                            "text": decrypted_text,
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

    def save_chat_session(self, session_id: str, channel_id: str | int, guild_id: str | int | None, user_id: str | int, history: list[dict[str, str]]):
        encrypted_history_json = encryption_manager.encrypt_text(json.dumps(history))
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO chat_sessions (session_id, channel_id, guild_id, creator_user_id, history_json, last_active_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(session_id) DO UPDATE SET
                    history_json = excluded.history_json,
                    last_active_at = CURRENT_TIMESTAMP
            """, (session_id, str(channel_id), str(guild_id) if guild_id else None, str(user_id), encrypted_history_json))
            conn.commit()

    def get_chat_session(self, session_id: str) -> list[dict[str, str]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT history_json FROM chat_sessions WHERE session_id = ?", (session_id,))
            row = cursor.fetchone()
            if row:
                try:
                    decrypted_json = encryption_manager.decrypt_text(row["history_json"])
                    return json.loads(decrypted_json)
                except Exception:
                    pass
        return []

    def delete_chat_session(self, session_id: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM chat_sessions WHERE session_id = ?", (session_id,))
            conn.commit()
            return cursor.rowcount > 0

    def add_staged_chat_context(self, channel_id: str | int, user_id: str | int, entry: dict[str, Any]) -> int:
        chan_str = str(channel_id)
        uid_str = str(user_id)
        msg_id_str = str(entry.get("id", "0"))
        author_name = str(entry.get("author", "User"))[:100]
        author_id = str(entry.get("author_id", "0"))
        raw_content = str(entry.get("content", ""))[:4000]
        enc_content = encryption_manager.encrypt_text(raw_content)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO staged_chat_context (channel_id, user_id, message_id, author_name, author_id, content)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (chan_str, uid_str, msg_id_str, author_name, author_id, enc_content))
            conn.commit()
            cursor.execute("SELECT COUNT(*) as cnt FROM staged_chat_context WHERE channel_id = ? AND user_id = ?", (chan_str, uid_str))
            row = cursor.fetchone()
            return row["cnt"] if row else 1

    def get_staged_chat_context(self, channel_id: str | int, user_id: str | int) -> list[dict[str, Any]]:
        chan_str = str(channel_id)
        uid_str = str(user_id)
        results = []
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, message_id, author_name, author_id, content, created_at
                FROM staged_chat_context
                WHERE channel_id = ? AND user_id = ?
                ORDER BY id ASC
            """, (chan_str, uid_str))
            for r in cursor.fetchall():
                dec_content = encryption_manager.decrypt_text(r["content"])
                results.append({
                    "id": r["message_id"],
                    "author": r["author_name"],
                    "author_id": r["author_id"],
                    "content": dec_content,
                    "created_at": r["created_at"]
                })
        return results

    def clear_staged_chat_context(self, channel_id: str | int, user_id: str | int) -> bool:
        chan_str = str(channel_id)
        uid_str = str(user_id)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM staged_chat_context WHERE channel_id = ? AND user_id = ?", (chan_str, uid_str))
            conn.commit()
            return cursor.rowcount > 0

    def pop_staged_chat_context(self, channel_id: str | int, user_id: str | int) -> list[dict[str, Any]]:
        chan_str = str(channel_id)
        uid_str = str(user_id)
        items = self.get_staged_chat_context(chan_str, uid_str)
        if items:
            self.clear_staged_chat_context(chan_str, uid_str)
        return items

memory_manager = MemoryManager()