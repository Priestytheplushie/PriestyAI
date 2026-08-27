import os
import time
import json
import difflib
import sqlite3
import asyncio
import logging
from typing import Any

logger = logging.getLogger("PriestyAI.BranchManager")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "priestyai.db")

def compute_code_diff(old_code: str, new_code: str, filename: str, v_old: int, v_new: int) -> tuple[str, int, int]:
    old_lines = old_code.splitlines(keepends=True)
    new_lines = new_code.splitlines(keepends=True)
    diff_lines = list(difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"{filename} (v{v_old})",
        tofile=f"{filename} (v{v_new})",
        n=3
    ))
    diff_text = "".join(diff_lines)
    additions = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
    deletions = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
    return diff_text, additions, deletions


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

            cursor.execute("SELECT versions_json FROM message_generations")
            for g_row in cursor.fetchall():
                try:
                    versions = json.loads(g_row["versions_json"] or "[]")
                    for v in versions:
                        for art in v.get("staged_artifacts", []):
                            if art.get("artifact_id") == str(artifact_id):
                                return art
                except Exception:
                    continue
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

    def get_channel_artifacts(self, channel_id: str | int, limit: int = 5) -> list[dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM conversation_artifacts WHERE channel_id = ? ORDER BY updated_at DESC LIMIT ?",
                (str(channel_id), limit)
            )
            results = []
            for row in cursor.fetchall():
                d = dict(row)
                d["versions"] = json.loads(d.get("versions_json") or "[]")
                results.append(d)
            return results

    def save_or_update_artifact(
        self,
        channel_id: str | int,
        filename: str,
        title: str,
        content: str,
        files: list[dict[str, Any]] | None = None,
        change_summary: str = ""
    ) -> dict[str, Any]:
        existing = self.get_artifact_by_channel_and_file(channel_id, filename)
        now_ts = int(time.time())

        clean_fn = filename.strip()
        clean_title = title or clean_fn
        lines = len(content.splitlines()) if content else sum(len(f.get("content", "").splitlines()) for f in (files or []))
        size_b = len(content.encode("utf-8")) if content else sum(len(f.get("content", "").encode("utf-8")) for f in (files or []))

        result_payload: dict[str, Any] = {}

        if existing:
            artifact_id = existing["artifact_id"]
            versions = existing.get("versions", [])
            v_old = len(versions)
            v_new = v_old + 1

            old_entry = versions[-1] if versions else {}
            old_code = old_entry.get("content", "")

            diff_text, additions, deletions = compute_code_diff(old_code, content, clean_fn, v_old, v_new)
            summary = change_summary.strip() or f"Updated implementation (v{v_new})"

            version_entry = {
                "version": v_new,
                "summary": summary,
                "content": content,
                "files": files or [],
                "lines": max(1, lines),
                "size_bytes": size_b,
                "diff": diff_text,
                "additions": additions,
                "deletions": deletions,
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
                """, (clean_title, v_new, json.dumps(versions), str(artifact_id)))
                conn.commit()

            logger.info(f"[Artifacts] Auto-updated '{clean_fn}' -> v{v_new} (+{additions} -{deletions})")
            result_payload = {
                "artifact_id": artifact_id,
                "filename": clean_fn,
                "title": clean_title,
                "active_version": v_new,
                "total_versions": len(versions),
                "versions": versions,
                "additions": additions,
                "deletions": deletions,
                "diff": diff_text,
                "is_update": True,
                "latest_version_data": version_entry
            }
        else:
            artifact_id = f"art_{int(time.time() * 1000)}"
            initial_version = {
                "version": 1,
                "summary": change_summary.strip() or "Initial implementation",
                "content": content,
                "files": files or [],
                "lines": max(1, lines),
                "size_bytes": size_b,
                "diff": "",
                "additions": 0,
                "deletions": 0,
                "timestamp": now_ts
            }
            versions = [initial_version]

            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO conversation_artifacts (
                        artifact_id, channel_id, filename, title, active_version, versions_json
                    ) VALUES (?, ?, ?, ?, 1, ?)
                """, (str(artifact_id), str(channel_id), clean_fn, clean_title, json.dumps(versions)))
                conn.commit()

            logger.info(f"[Artifacts] Created initial '{clean_fn}' (v1)")
            result_payload = {
                "artifact_id": artifact_id,
                "filename": clean_fn,
                "title": clean_title,
                "active_version": 1,
                "total_versions": 1,
                "versions": versions,
                "additions": 0,
                "deletions": 0,
                "diff": "",
                "is_update": False,
                "latest_version_data": initial_version
            }

        try:
            from core.playground_server import playground_server
            loop = asyncio.get_running_loop()
            loop.create_task(playground_server.notify_artifact_updated(result_payload["artifact_id"], result_payload))
        except (RuntimeError, Exception):
            pass

        return result_payload


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
        if "message_ids" not in initial_version_data or not initial_version_data["message_ids"]:
            initial_version_data["message_ids"] = [str(message_id)]

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
        mid_str = str(message_id)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM message_generations WHERE message_id = ?", (mid_str,))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["attachments"] = json.loads(d.get("attachments_json") or "[]")
                d["versions"] = json.loads(d.get("versions_json") or "[]")
                return d

            cursor.execute("SELECT * FROM message_generations WHERE versions_json LIKE ?", (f'%"{mid_str}"%',))
            for row in cursor.fetchall():
                d = dict(row)
                d["attachments"] = json.loads(d.get("attachments_json") or "[]")
                d["versions"] = json.loads(d.get("versions_json") or "[]")
                for v in d["versions"]:
                    if mid_str in [str(x) for x in v.get("message_ids", [])]:
                        return d
        return None

    def add_retry_version(self, message_id: str | int, new_version_data: dict[str, Any]) -> int:
        gen = self.get_generation(message_id)
        if not gen:
            return 1

        root_id = gen["message_id"]
        versions = gen.get("versions", [])
        new_version_data["version_idx"] = len(versions) + 1
        
        if "message_ids" not in new_version_data or not new_version_data["message_ids"]:
            new_version_data["message_ids"] = [str(root_id)]

        versions.append(new_version_data)
        new_active_idx = len(versions)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE message_generations 
                SET versions_json = ?, active_version = ?
                WHERE message_id = ?
            """, (json.dumps(versions), new_active_idx, str(root_id)))
            conn.commit()

        return new_active_idx

    def update_version_data(self, message_id: str | int, version_idx: int, updated_data: dict[str, Any]):
        gen = self.get_generation(message_id)
        if not gen:
            return

        root_id = gen["message_id"]
        versions = gen.get("versions", [])
        if 1 <= version_idx <= len(versions):
            versions[version_idx - 1] = updated_data
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE message_generations SET versions_json = ? WHERE message_id = ?
                """, (json.dumps(versions), str(root_id)))
                conn.commit()

    def set_active_version(self, message_id: str | int, version_idx: int) -> dict[str, Any] | None:
        gen = self.get_generation(message_id)
        if not gen:
            return None

        root_id = gen["message_id"]
        versions = gen.get("versions", [])
        if 1 <= version_idx <= len(versions):
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE message_generations SET active_version = ? WHERE message_id = ?
                """, (version_idx, str(root_id)))
                conn.commit()
            return versions[version_idx - 1]
        return None

    def update_active_version_content(self, message_id: str | int, new_content: str, new_attachments: list[dict[str, Any]] | None = None):
        gen = self.get_generation(message_id)
        if not gen:
            return

        root_id = gen["message_id"]
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
                """, (json.dumps(versions), str(root_id)))
                conn.commit()

branch_manager = BranchManager()