import os
import json
import sqlite3
import logging
from typing import Any
from core.encryption import encryption_manager

logger = logging.getLogger("PriestyAI.FeedbackManager")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "priestyai.db")

class FeedbackManager:
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
                CREATE TABLE IF NOT EXISTS feedback_submissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    user_name TEXT NOT NULL,
                    guild_id TEXT,
                    channel_id TEXT,
                    feedback_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    attachments_json TEXT DEFAULT '[]',
                    status TEXT DEFAULT 'open',
                    admin_notes TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_feedback_user ON feedback_submissions(user_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_feedback_status ON feedback_submissions(status)")
            conn.commit()

    def submit_feedback(
        self,
        user_id: str | int,
        user_name: str,
        guild_id: str | int | None,
        channel_id: str | int | None,
        feedback_type: str,
        content: str,
        attachments: list[dict[str, str]] | None = None
    ) -> int:
        encrypted_content = encryption_manager.encrypt_text(content.strip())
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO feedback_submissions (
                    user_id, user_name, guild_id, channel_id,
                    feedback_type, content, attachments_json, status, admin_notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', '')
            """, (
                str(user_id),
                user_name.strip(),
                str(guild_id) if guild_id else None,
                str(channel_id) if channel_id else None,
                feedback_type.strip(),
                encrypted_content,
                json.dumps(attachments or [])
            ))
            conn.commit()
            ticket_id = cursor.lastrowid
        logger.info(f"[Feedback] Recorded ticket #{ticket_id} ({feedback_type}) from {user_name} ({user_id})")
        return ticket_id

    def get_feedback_ticket(self, ticket_id: int) -> dict[str, Any] | None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM feedback_submissions WHERE id = ?", (ticket_id,))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["content"] = encryption_manager.decrypt_text(d.get("content", ""))
                d["attachments"] = json.loads(d.get("attachments_json") or "[]")
                return d
        return None

    def get_all_feedback(self, status_filter: str | None = None, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if status_filter and status_filter.lower() != "all":
                cursor.execute(
                    "SELECT * FROM feedback_submissions WHERE status = ? ORDER BY id DESC LIMIT ? OFFSET ?",
                    (status_filter.lower(), limit, offset)
                )
            else:
                cursor.execute(
                    "SELECT * FROM feedback_submissions ORDER BY id DESC LIMIT ? OFFSET ?",
                    (limit, offset)
                )
            rows = cursor.fetchall()
            results = []
            for row in rows:
                d = dict(row)
                d["content"] = encryption_manager.decrypt_text(d.get("content", ""))
                d["attachments"] = json.loads(d.get("attachments_json") or "[]")
                results.append(d)
            return results

    def get_feedback_count(self, status_filter: str | None = None) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if status_filter and status_filter.lower() != "all":
                cursor.execute("SELECT COUNT(*) as cnt FROM feedback_submissions WHERE status = ?", (status_filter.lower(),))
            else:
                cursor.execute("SELECT COUNT(*) as cnt FROM feedback_submissions")
            row = cursor.fetchone()
            return row["cnt"] if row else 0

    def update_ticket_status(self, ticket_id: int, new_status: str, admin_notes: str | None = None) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if admin_notes is not None:
                cursor.execute(
                    "UPDATE feedback_submissions SET status = ?, admin_notes = ? WHERE id = ?",
                    (new_status.lower(), admin_notes.strip(), ticket_id)
                )
            else:
                cursor.execute(
                    "UPDATE feedback_submissions SET status = ? WHERE id = ?",
                    (new_status.lower(), ticket_id)
                )
            conn.commit()
            return cursor.rowcount > 0

    def delete_ticket(self, ticket_id: int) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM feedback_submissions WHERE id = ?", (ticket_id,))
            conn.commit()
            return cursor.rowcount > 0


    def get_all_tables(self) -> list[dict[str, Any]]:
        tables = []
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name ASC")
            rows = cursor.fetchall()
            for r in rows:
                t_name = r["name"]
                try:
                    cursor.execute(f"SELECT COUNT(*) as cnt FROM {t_name}")
                    count_row = cursor.fetchone()
                    count = count_row["cnt"] if count_row else 0
                except Exception:
                    count = 0
                tables.append({"name": t_name, "row_count": count})
        return tables

    def get_table_schema(self, table_name: str) -> list[str]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"PRAGMA table_info({table_name})")
            return [col["name"] for col in cursor.fetchall()]

    def get_table_rows(self, table_name: str, limit: int = 4, offset: int = 0) -> list[dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(f"SELECT * FROM {table_name} LIMIT ? OFFSET ?", (limit, offset))
                rows = cursor.fetchall()
                results = []
                for row in rows:
                    row_dict = dict(row)
                    for key, val in row_dict.items():
                        if isinstance(val, str) and val.startswith("gAAAAA"):
                            row_dict[key] = encryption_manager.decrypt_text(val)
                    results.append(row_dict)
                return results
            except Exception as e:
                logger.error(f"Error querying table {table_name}: {e}")
                return []

feedback_manager = FeedbackManager()