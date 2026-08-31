import os
import time
import json
import base64
import difflib
import sqlite3
import asyncio
import logging
from typing import Any

logger = logging.getLogger("PriestyAI.BranchManager")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "priestyai.db")

def safe_json_default(obj: Any) -> Any:
    if isinstance(obj, bytes):
        return base64.b64encode(obj).decode("utf-8")
    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        return obj.to_dict()
    return str(obj)

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

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS quizzes (
                    quiz_id TEXT PRIMARY KEY,
                    channel_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    difficulty TEXT DEFAULT 'Medium',
                    questions_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_quizzes_channel ON quizzes(channel_id)")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS quiz_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    quiz_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    score INTEGER DEFAULT 0,
                    total_questions INTEGER NOT NULL,
                    skipped INTEGER DEFAULT 0,
                    is_completed INTEGER DEFAULT 0,
                    current_idx INTEGER DEFAULT 0,
                    headline TEXT DEFAULT 'Solid progress! Keep up the good work.',
                    answers_json TEXT NOT NULL,
                    strengths_json TEXT DEFAULT '[]',
                    focus_areas_json TEXT DEFAULT '[]',
                    completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_quiz_attempts_user ON quiz_attempts(quiz_id, user_id)")

            cursor.execute("PRAGMA table_info(quiz_attempts)")
            att_columns = [row["name"] for row in cursor.fetchall()]
            if "skipped" not in att_columns and "attempt_id" in att_columns:
                cursor.execute("ALTER TABLE quiz_attempts ADD COLUMN skipped INTEGER DEFAULT 0")
            if "headline" not in att_columns and "attempt_id" in att_columns:
                cursor.execute("ALTER TABLE quiz_attempts ADD COLUMN headline TEXT DEFAULT 'Solid progress! Keep up the good work.'")
            if "is_completed" not in att_columns and "attempt_id" in att_columns:
                cursor.execute("ALTER TABLE quiz_attempts ADD COLUMN is_completed INTEGER DEFAULT 0")
            if "current_idx" not in att_columns and "attempt_id" in att_columns:
                cursor.execute("ALTER TABLE quiz_attempts ADD COLUMN current_idx INTEGER DEFAULT 0")

            cursor.execute("""
                DELETE FROM conversation_artifacts 
                WHERE filename = 'study_guide.md' AND channel_id NOT LIKE 'quiz_guide_%'
            """)

            conn.commit()

    def save_quiz(
        self,
        channel_id: str | int,
        title: str,
        topic: str,
        difficulty: str,
        questions: list[dict[str, Any]],
        quiz_id: str | None = None
    ) -> dict[str, Any]:
        q_id = quiz_id or f"quiz_{int(time.time() * 1000)}"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO quizzes (quiz_id, channel_id, title, topic, difficulty, questions_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(quiz_id) DO UPDATE SET
                    title = excluded.title,
                    topic = excluded.topic,
                    difficulty = excluded.difficulty,
                    questions_json = excluded.questions_json
            """, (
                str(q_id),
                str(channel_id),
                title.strip() or "Quiz",
                topic.strip() or "General Knowledge",
                difficulty.strip().capitalize() or "Medium",
                json.dumps(questions, default=safe_json_default)
            ))
            conn.commit()

        logger.info(f"[BranchManager] Saved quiz '{title}' ({q_id}) with {len(questions)} questions.")
        return {
            "quiz_id": str(q_id),
            "channel_id": str(channel_id),
            "title": title.strip() or "Quiz",
            "topic": topic.strip() or "General Knowledge",
            "difficulty": difficulty.strip().capitalize() or "Medium",
            "questions": questions,
            "status": "ready"
        }

    def get_quiz(self, quiz_id: str) -> dict[str, Any] | None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM quizzes WHERE quiz_id = ?", (str(quiz_id),))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["questions"] = json.loads(d.get("questions_json") or "[]")
                return d
        return None

    def save_quiz_attempt_progress(
        self,
        quiz_id: str,
        user_id: str | int,
        current_idx: int,
        answers: dict[int, int | None],
        total_questions: int = 0
    ) -> str:
        attempt_id = f"att_{quiz_id}_{user_id}"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO quiz_attempts (
                    attempt_id, quiz_id, user_id, score, total_questions,
                    skipped, is_completed, current_idx, headline, answers_json, strengths_json, focus_areas_json
                ) VALUES (?, ?, ?, 0, ?, 0, 0, ?, '', ?, '[]', '[]')
                ON CONFLICT(attempt_id) DO UPDATE SET
                    current_idx = excluded.current_idx,
                    answers_json = excluded.answers_json,
                    completed_at = CURRENT_TIMESTAMP
            """, (
                attempt_id,
                str(quiz_id),
                str(user_id),
                int(total_questions),
                int(current_idx),
                json.dumps(answers, default=safe_json_default)
            ))
            conn.commit()
        return attempt_id

    def finalize_quiz_attempt(
        self,
        quiz_id: str,
        user_id: str | int,
        score: int,
        total_questions: int,
        skipped: int,
        headline: str,
        answers: dict[int, int | None],
        strengths: list[str],
        focus_areas: list[str]
    ) -> str:
        attempt_id = f"att_{quiz_id}_{user_id}"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO quiz_attempts (
                    attempt_id, quiz_id, user_id, score, total_questions,
                    skipped, is_completed, current_idx, headline, answers_json, strengths_json, focus_areas_json
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                ON CONFLICT(attempt_id) DO UPDATE SET
                    score = excluded.score,
                    total_questions = excluded.total_questions,
                    skipped = excluded.skipped,
                    is_completed = 1,
                    current_idx = excluded.current_idx,
                    headline = excluded.headline,
                    answers_json = excluded.answers_json,
                    strengths_json = excluded.strengths_json,
                    focus_areas_json = excluded.focus_areas_json,
                    completed_at = CURRENT_TIMESTAMP
            """, (
                attempt_id,
                str(quiz_id),
                str(user_id),
                int(score),
                int(total_questions),
                int(skipped),
                int(total_questions - 1),
                headline.strip(),
                json.dumps(answers, default=safe_json_default),
                json.dumps(strengths, default=safe_json_default),
                json.dumps(focus_areas, default=safe_json_default)
            ))
            conn.commit()
        logger.info(f"[BranchManager] Finalized quiz attempt '{attempt_id}' for user {user_id} on quiz {quiz_id} (Score: {score}/{total_questions})")
        return attempt_id

    def get_quiz_attempt(self, quiz_id: str, user_id: str | int) -> dict[str, Any] | None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM quiz_attempts WHERE quiz_id = ? AND user_id = ? ORDER BY completed_at DESC LIMIT 1",
                (str(quiz_id), str(user_id))
            )
            row = cursor.fetchone()
            if row:
                d = dict(row)
                raw_ans = json.loads(d.get("answers_json") or "{}")
                parsed_ans = {}
                for k, v in raw_ans.items():
                    try:
                        parsed_ans[int(k)] = v
                    except ValueError:
                        parsed_ans[k] = v
                d["answers"] = parsed_ans
                d["strengths"] = json.loads(d.get("strengths_json") or "[]")
                d["focus_areas"] = json.loads(d.get("focus_areas_json") or "[]")
                return d
        return None

    def delete_quiz_attempts_for_user(self, quiz_id: str, user_id: str | int) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM quiz_attempts WHERE quiz_id = ? AND user_id = ?",
                (str(quiz_id), str(user_id))
            )
            conn.commit()
            return cursor.rowcount > 0

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
                "SELECT * FROM conversation_artifacts WHERE channel_id = ? AND filename != 'study_guide.md' ORDER BY updated_at DESC LIMIT ?",
                (str(channel_id), limit)
            )
            results = []
            for row in cursor.fetchall():
                d = dict(row)
                d["versions"] = json.loads(d.get("versions_json") or "[]")
                results.append(d)
            return results

    def update_artifact_content_in_place(
        self,
        artifact_id: str,
        files: list[dict[str, Any]],
        target_version: int | None = None
    ) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM conversation_artifacts WHERE artifact_id = ?", (str(artifact_id),))
            row = cursor.fetchone()
            if not row:
                return False

            versions = json.loads(row["versions_json"] or "[]")
            if not versions:
                return False

            active_v = target_version or row["active_version"] or len(versions)
            if not (1 <= active_v <= len(versions)):
                active_v = len(versions)

            v_entry = versions[active_v - 1]
            v_entry["files"] = files
            
            if len(files) == 1 and not files[0].get("filename", "").endswith(".zip"):
                v_entry["content"] = files[0].get("content", "")
                v_entry["lines"] = max(1, len(v_entry["content"].splitlines()))
                v_entry["size_bytes"] = len(v_entry["content"].encode("utf-8"))
            else:
                v_entry["content"] = ""
                v_entry["lines"] = sum(f.get("lines", len(f.get("content", "").splitlines())) for f in files)
                v_entry["size_bytes"] = sum(f.get("size_bytes", len(f.get("content", "").encode("utf-8"))) for f in files)

            v_entry["timestamp"] = int(time.time())

            cursor.execute("""
                UPDATE conversation_artifacts
                SET versions_json = ?, updated_at = CURRENT_TIMESTAMP
                WHERE artifact_id = ?
            """, (json.dumps(versions, default=safe_json_default), str(artifact_id)))
            conn.commit()
            logger.info(f"[BranchManager] In-place auto-saved artifact '{artifact_id}' (v{active_v}, {len(files)} files)")
            return True

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
                """, (clean_title, v_new, json.dumps(versions, default=safe_json_default), str(artifact_id)))
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
                """, (str(artifact_id), str(channel_id), clean_fn, clean_title, json.dumps(versions, default=safe_json_default)))
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
                json.dumps(messages, default=safe_json_default)
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
            cursor.execute("UPDATE branches SET messages_json = ? WHERE thread_id = ?", (json.dumps(msgs, default=safe_json_default), str(thread_id)))
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
                cursor.execute("UPDATE branches SET messages_json = ? WHERE branch_id = ?", (json.dumps(msgs, default=safe_json_default), str(branch_id)))
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
                json.dumps(attachments, default=safe_json_default),
                context_xml,
                json.dumps(versions, default=safe_json_default)
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
            """, (json.dumps(versions, default=safe_json_default), new_active_idx, str(root_id)))
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
                """, (json.dumps(versions, default=safe_json_default), str(root_id)))
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
                """, (json.dumps(versions, default=safe_json_default), str(root_id)))
                conn.commit()

branch_manager = BranchManager()