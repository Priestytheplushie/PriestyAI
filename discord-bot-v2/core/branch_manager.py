import os
import time
import json
import base64
import sqlite3
import logging
from typing import Any
import discord

logger = logging.getLogger("PriestyAI.BranchManager")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "priestyai.db")

class BranchManager:
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
                CREATE TABLE IF NOT EXISTS branches (
                    branch_id TEXT PRIMARY KEY,
                    thread_id TEXT UNIQUE NOT NULL,
                    channel_id TEXT NOT NULL,
                    guild_id TEXT,
                    creator_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    root_message_id TEXT NOT NULL,
                    messages_json TEXT NOT NULL,
                    is_active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_branches_thread ON branches(thread_id)")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS message_generations (
                    message_id TEXT PRIMARY KEY,
                    channel_id TEXT NOT NULL,
                    guild_id TEXT,
                    author_id TEXT NOT NULL,
                    prompt_text TEXT NOT NULL,
                    attachments_json TEXT DEFAULT '[]',
                    context_xml TEXT,
                    active_version INTEGER DEFAULT 1,
                    versions_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS conversation_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    channel_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    title TEXT NOT NULL,
                    active_version INTEGER DEFAULT 1,
                    versions_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_channel_file ON conversation_artifacts(channel_id, filename)")
            conn.commit()


    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM conversation_artifacts WHERE artifact_id = ?", (str(artifact_id),))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["versions"] = json.loads(d.get("versions_json") or "[]")
                return d
        return None

    def get_artifact_by_channel_and_file(self, channel_id: str | int, filename: str) -> dict[str, Any] | None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM conversation_artifacts WHERE channel_id = ? AND filename = ? ORDER BY updated_at DESC LIMIT 1",
                (str(channel_id), filename.strip())
            )
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["versions"] = json.loads(d.get("versions_json") or "[]")
                return d
        return None

    def save_or_update_artifact(
        self,
        channel_id: str | int,
        filename: str,
        title: str,
        content: str,
        files: list[dict[str, Any]] | None = None,
        change_summary: str = "",
        is_update: bool = False
    ) -> dict[str, Any]:
        existing = self.get_artifact_by_channel_and_file(channel_id, filename) if is_update else None
        now_ts = int(time.time())

        if existing:
            artifact_id = existing["artifact_id"]
            versions = existing.get("versions", [])
            new_v_num = len(versions) + 1
            
            lines = len(content.splitlines()) if content else sum(len(f.get("content", "").splitlines()) for f in (files or []))
            size_b = len(content.encode("utf-8")) if content else sum(len(f.get("content", "").encode("utf-8")) for f in (files or []))

            version_entry = {
                "version": new_v_num,
                "summary": change_summary.strip() or f"Updated {filename}",
                "content": content,
                "files": files or [],
                "lines": max(1, lines),
                "size_bytes": size_b,
                "timestamp": now_ts
            }

            versions.append(version_entry)
            if len(versions) > 25:
                versions = versions[-25:]

            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE conversation_artifacts
                    SET title = ?, active_version = ?, versions_json = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE artifact_id = ?
                """, (title or existing.get("title", filename), new_v_num, json.dumps(versions), str(artifact_id)))
                conn.commit()

            return {
                "artifact_id": artifact_id,
                "filename": filename,
                "title": title or existing.get("title", filename),
                "active_version": new_v_num,
                "total_versions": len(versions),
                "versions": versions,
                "latest_version_data": version_entry
            }
        else:
            artifact_id = f"art_{int(time.time() * 1000)}"
            lines = len(content.splitlines()) if content else sum(len(f.get("content", "").splitlines()) for f in (files or []))
            size_b = len(content.encode("utf-8")) if content else sum(len(f.get("content", "").encode("utf-8")) for f in (files or []))

            initial_version = {
                "version": 1,
                "summary": change_summary.strip() or "Initial implementation",
                "content": content,
                "files": files or [],
                "lines": max(1, lines),
                "size_bytes": size_b,
                "timestamp": now_ts
            }
            versions = [initial_version]

            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO conversation_artifacts (
                        artifact_id, channel_id, filename, title, active_version, versions_json
                    ) VALUES (?, ?, ?, ?, 1, ?)
                """, (str(artifact_id), str(channel_id), filename.strip(), title or filename, json.dumps(versions)))
                conn.commit()

            return {
                "artifact_id": artifact_id,
                "filename": filename,
                "title": title or filename,
                "active_version": 1,
                "total_versions": 1,
                "versions": versions,
                "latest_version_data": initial_version
            }


    def create_branch(
        self,
        branch_id: str,
        thread_id: str | int,
        channel_id: str | int,
        guild_id: str | int | None,
        creator_id: str | int,
        title: str,
        root_message_id: str | int,
        messages: list[dict[str, Any]]
    ):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO branches (
                    branch_id, thread_id, channel_id, guild_id,
                    creator_id, title, root_message_id, messages_json, is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(branch_id) DO UPDATE SET
                    thread_id = excluded.thread_id,
                    messages_json = excluded.messages_json,
                    is_active = 1
            """, (
                str(branch_id),
                str(thread_id),
                str(channel_id),
                str(guild_id) if guild_id else None,
                str(creator_id),
                title,
                str(root_message_id),
                json.dumps(messages)
            ))
            conn.commit()

    def get_branch_by_thread_id(self, thread_id: str | int) -> dict[str, Any] | None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM branches WHERE thread_id = ? AND is_active = 1", (str(thread_id),))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["messages"] = json.loads(d.get("messages_json") or "[]")
                return d
        return None

    def get_branch_by_id(self, branch_id: str) -> dict[str, Any] | None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM branches WHERE branch_id = ?", (str(branch_id),))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["messages"] = json.loads(d.get("messages_json") or "[]")
                return d
        return None

    def add_branch_message(self, thread_id: str | int, role: str, author_name: str, author_id: str | int, content: str):
        branch = self.get_branch_by_thread_id(thread_id)
        if not branch:
            return

        msgs = branch.get("messages", [])
        msgs.append({
            "id": str(int(time.time() * 1000)),
            "role": role,
            "author": author_name,
            "author_id": str(author_id),
            "content": content,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        })

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE branches SET messages_json = ? WHERE thread_id = ?", (json.dumps(msgs), str(thread_id)))
            conn.commit()

    def prune_branch_message(self, branch_id: str, message_index: int) -> bool:
        branch = self.get_branch_by_id(branch_id)
        if not branch:
            return False

        msgs = branch.get("messages", [])
        if 0 <= message_index < len(msgs):
            msgs.pop(message_index)
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE branches SET messages_json = ? WHERE branch_id = ?", (json.dumps(msgs), str(branch_id)))
                conn.commit()
            return True
        return False

    def delete_branch(self, branch_id: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE branches SET is_active = 0 WHERE branch_id = ?", (str(branch_id),))
            conn.commit()


    def save_generation(
        self,
        message_id: str | int,
        channel_id: str | int,
        guild_id: str | int | None,
        author_id: str | int,
        prompt_text: str,
        attachments: list[dict[str, Any]],
        context_xml: str,
        initial_version_data: dict[str, Any]
    ):
        versions = [initial_version_data]
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO message_generations (
                    message_id, channel_id, guild_id, author_id,
                    prompt_text, attachments_json, context_xml,
                    active_version, versions_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(message_id) DO UPDATE SET
                    versions_json = excluded.versions_json,
                    active_version = excluded.active_version
            """, (
                str(message_id),
                str(channel_id),
                str(guild_id) if guild_id else None,
                str(author_id),
                prompt_text,
                json.dumps(attachments),
                context_xml,
                json.dumps(versions)
            ))
            conn.commit()

    def get_generation(self, message_id: str | int) -> dict[str, Any] | None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM message_generations WHERE message_id = ?", (str(message_id),))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["attachments"] = json.loads(d.get("attachments_json") or "[]")
                d["versions"] = json.loads(d.get("versions_json") or "[]")
                return d
        return None

    def add_retry_version(self, message_id: str | int, new_version_data: dict[str, Any]) -> int:
        gen = self.get_generation(message_id)
        if not gen:
            return 1

        versions = gen.get("versions", [])
        new_version_data["version_idx"] = len(versions) + 1
        versions.append(new_version_data)
        new_active_idx = len(versions)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE message_generations 
                SET versions_json = ?, active_version = ?
                WHERE message_id = ?
            """, (json.dumps(versions), new_active_idx, str(message_id)))
            conn.commit()

        return new_active_idx

    def set_active_version(self, message_id: str | int, version_idx: int) -> dict[str, Any] | None:
        gen = self.get_generation(message_id)
        if not gen:
            return None

        versions = gen.get("versions", [])
        if 1 <= version_idx <= len(versions):
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE message_generations SET active_version = ? WHERE message_id = ?
                """, (version_idx, str(message_id)))
                conn.commit()
            return versions[version_idx - 1]
        return None

    def update_active_version_content(self, message_id: str | int, new_content: str, new_attachments: list[dict[str, Any]] | None = None):
        gen = self.get_generation(message_id)
        if not gen:
            return

        active_idx = gen.get("active_version", 1)
        versions = gen.get("versions", [])
        if 1 <= active_idx <= len(versions):
            versions[active_idx - 1]["content"] = new_content
            if new_attachments is not None:
                versions[active_idx - 1]["attachments"] = new_attachments

            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE message_generations SET versions_json = ? WHERE message_id = ?
                """, (json.dumps(versions), str(message_id)))
                conn.commit()

branch_manager = BranchManager()