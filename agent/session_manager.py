import os
import io
import sys
import json
import time
import shutil
import zipfile
import sqlite3
import asyncio
import logging
from typing import Any
import httpx
import discord
from agent.constants import GITHUB_BOT_NAME, GITHUB_BOT_EMAIL
from config.settings import AGENT_WORKSPACES_ROOT, GITHUB_TOKEN

logger = logging.getLogger("PriestyAI.Agent.SessionManager")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "priestyai.db")

PRIMARY_POLYGLOT_IMAGE = "nikolaik/python-nodejs:python3.11-nodejs20-slim"
FALLBACK_DOCKER_IMAGE = "python:3.11-bookworm"

def normalize_repo_url(repo_input: str) -> tuple[str, str, str]:
    clean = repo_input.strip().rstrip("/")
    if not clean:
        return "", "", ""

    if "github.com/" in clean:
        parts = clean.split("github.com/")[-1].split("/")
        if len(parts) >= 2:
            owner = parts[0]
            repo = parts[1].replace(".git", "")
            return f"https://github.com/{owner}/{repo}.git", owner, repo

    parts = clean.split("/")
    if len(parts) == 2 and not clean.startswith("http"):
        owner = parts[0]
        repo = parts[1].replace(".git", "")
        return f"https://github.com/{owner}/{repo}.git", owner, repo

    if clean.startswith("http"):
        repo_name = clean.split("/")[-1].replace(".git", "")
        return clean, "", repo_name

    return f"https://github.com/{clean}.git", "", clean

class AgentSessionManager:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._abort_events: dict[str, asyncio.Event] = {}
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
                CREATE TABLE IF NOT EXISTS agent_sessions (
                    session_id TEXT PRIMARY KEY,
                    thread_id TEXT UNIQUE NOT NULL,
                    channel_id TEXT NOT NULL,
                    guild_id TEXT NOT NULL,
                    creator_id TEXT NOT NULL,
                    collaborators_json TEXT NOT NULL,
                    repo_url TEXT DEFAULT '',
                    initial_prompt TEXT NOT NULL,
                    state TEXT DEFAULT 'planning',
                    active_plan_version INTEGER DEFAULT 1,
                    container_id TEXT DEFAULT '',
                    workspace_path TEXT NOT NULL,
                    witty_statuses_json TEXT NOT NULL,
                    last_plan_message_id TEXT DEFAULT '',
                    last_completed_message_id TEXT DEFAULT '',
                    header_message_id TEXT DEFAULT '',
                    review_message_id TEXT DEFAULT '',
                    github_pr_data_json TEXT DEFAULT '{}',
                    signoffs_json TEXT DEFAULT '{}',
                    pr_url TEXT DEFAULT '',
                    pr_number INTEGER DEFAULT 0,
                    is_coding_task INTEGER DEFAULT 1,
                    task_type TEXT DEFAULT 'general',
                    citations_json TEXT DEFAULT '[]',
                    tasks_history_json TEXT DEFAULT '[]',
                    thread_title TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_active_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_agent_thread ON agent_sessions(thread_id)")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS agent_step_logs (
                    step_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    step_index INTEGER NOT NULL,
                    tool_name TEXT NOT NULL,
                    args_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    diff_text TEXT DEFAULT '',
                    additions INTEGER DEFAULT 0,
                    deletions INTEGER DEFAULT 0,
                    duration_ms INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_agent_step_session ON agent_step_logs(session_id, step_index)")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS agent_thoughts (
                    session_id TEXT PRIMARY KEY,
                    thoughts TEXT DEFAULT '',
                    tool_calls_json TEXT DEFAULT '[]',
                    duration_seconds INTEGER DEFAULT 1,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("PRAGMA table_info(agent_sessions)")
            columns = [row["name"] for row in cursor.fetchall()]
            if columns:
                if "header_message_id" not in columns:
                    cursor.execute("ALTER TABLE agent_sessions ADD COLUMN header_message_id TEXT DEFAULT ''")
                if "last_plan_message_id" not in columns:
                    cursor.execute("ALTER TABLE agent_sessions ADD COLUMN last_plan_message_id TEXT DEFAULT ''")
                if "last_completed_message_id" not in columns:
                    cursor.execute("ALTER TABLE agent_sessions ADD COLUMN last_completed_message_id TEXT DEFAULT ''")
                if "review_message_id" not in columns:
                    cursor.execute("ALTER TABLE agent_sessions ADD COLUMN review_message_id TEXT DEFAULT ''")
                if "github_pr_data_json" not in columns:
                    cursor.execute("ALTER TABLE agent_sessions ADD COLUMN github_pr_data_json TEXT DEFAULT '{}'")
                if "signoffs_json" not in columns:
                    cursor.execute("ALTER TABLE agent_sessions ADD COLUMN signoffs_json TEXT DEFAULT '{}'")
                if "pr_url" not in columns:
                    cursor.execute("ALTER TABLE agent_sessions ADD COLUMN pr_url TEXT DEFAULT ''")
                if "pr_number" not in columns:
                    cursor.execute("ALTER TABLE agent_sessions ADD COLUMN pr_number INTEGER DEFAULT 0")
                if "is_coding_task" not in columns:
                    cursor.execute("ALTER TABLE agent_sessions ADD COLUMN is_coding_task INTEGER DEFAULT 1")
                if "task_type" not in columns:
                    cursor.execute("ALTER TABLE agent_sessions ADD COLUMN task_type TEXT DEFAULT 'general'")
                if "citations_json" not in columns:
                    cursor.execute("ALTER TABLE agent_sessions ADD COLUMN citations_json TEXT DEFAULT '[]'")
                if "tasks_history_json" not in columns:
                    cursor.execute("ALTER TABLE agent_sessions ADD COLUMN tasks_history_json TEXT DEFAULT '[]'")
                if "thread_title" not in columns:
                    cursor.execute("ALTER TABLE agent_sessions ADD COLUMN thread_title TEXT DEFAULT ''")

            conn.commit()
        logger.info(f"[AgentSessionManager] Storage configured at '{AGENT_WORKSPACES_ROOT}'")

    def get_abort_event(self, session_id: str) -> asyncio.Event:
        if session_id not in self._abort_events:
            self._abort_events[session_id] = asyncio.Event()
        return self._abort_events[session_id]

    def trigger_abort(self, session_id: str) -> bool:
        if session_id in self._abort_events:
            self._abort_events[session_id].set()
            logger.info(f"[AgentSession] Triggered abort signal for session #{session_id}")
            return True
        return False

    def clear_abort_event(self, session_id: str):
        if session_id in self._abort_events:
            self._abort_events[session_id].clear()
        else:
            self._abort_events[session_id] = asyncio.Event()

    def create_session(
        self,
        session_id: str,
        thread_id: str | int,
        channel_id: str | int,
        guild_id: str | int,
        creator_id: str | int,
        collaborators: list[str | int],
        repo_url: str,
        initial_prompt: str,
        witty_statuses: list[str],
        is_coding_task: bool = True,
        task_type: str = "general",
        thread_title: str = ""
    ) -> dict[str, Any]:
        workspace_dir = os.path.join(AGENT_WORKSPACES_ROOT, session_id)
        os.makedirs(workspace_dir, exist_ok=True)

        clean_collabs = list({str(c) for c in collaborators + [creator_id]})
        norm_clone_url, _, _ = normalize_repo_url(repo_url)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO agent_sessions (
                    session_id, thread_id, channel_id, guild_id,
                    creator_id, collaborators_json, repo_url, initial_prompt,
                    state, active_plan_version, workspace_path, witty_statuses_json,
                    is_coding_task, task_type, citations_json, tasks_history_json, thread_title,
                    github_pr_data_json, signoffs_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'planning', 1, ?, ?, ?, ?, '[]', '[]', ?, '{}', '{}')
            """, (
                session_id,
                str(thread_id),
                str(channel_id),
                str(guild_id),
                str(creator_id),
                json.dumps(clean_collabs),
                norm_clone_url or repo_url.strip(),
                initial_prompt.strip(),
                workspace_dir,
                json.dumps(witty_statuses),
                int(is_coding_task),
                task_type,
                thread_title.strip()
            ))
            conn.commit()

        self.clear_abort_event(session_id)
        logger.info(f"[AgentSession] Created session #{session_id} (Title: '{thread_title}') for thread {thread_id}")
        return self.get_session_by_id(session_id)

    def get_session_by_id(self, session_id: str) -> dict[str, Any] | None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM agent_sessions WHERE session_id = ?", (session_id,))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["collaborators"] = json.loads(d.get("collaborators_json") or "[]")
                d["witty_statuses"] = json.loads(d.get("witty_statuses_json") or "[]")
                d["citations"] = json.loads(d.get("citations_json") or "[]")
                d["tasks_history"] = json.loads(d.get("tasks_history_json") or "[]")
                d["github_pr_data"] = json.loads(d.get("github_pr_data_json") or "{}")
                d["signoffs"] = json.loads(d.get("signoffs_json") or "{}")
                return d
        return None

    def get_session_by_thread_id(self, thread_id: str | int) -> dict[str, Any] | None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM agent_sessions WHERE thread_id = ?", (str(thread_id),))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["collaborators"] = json.loads(d.get("collaborators_json") or "[]")
                d["witty_statuses"] = json.loads(d.get("witty_statuses_json") or "[]")
                d["citations"] = json.loads(d.get("citations_json") or "[]")
                d["tasks_history"] = json.loads(d.get("tasks_history_json") or "[]")
                d["github_pr_data"] = json.loads(d.get("github_pr_data_json") or "{}")
                d["signoffs"] = json.loads(d.get("signoffs_json") or "{}")
                return d
        return None

    def get_active_sessions_for_repo(self, repo_owner: str, repo_name: str) -> list[dict[str, Any]]:
        target_sub = f"{repo_owner.lower()}/{repo_name.lower()}"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM agent_sessions WHERE repo_url != ''")
            matches = []
            for row in cursor.fetchall():
                d = dict(row)
                r_url = d.get("repo_url", "").lower()
                if target_sub in r_url:
                    d["collaborators"] = json.loads(d.get("collaborators_json") or "[]")
                    d["witty_statuses"] = json.loads(d.get("witty_statuses_json") or "[]")
                    d["citations"] = json.loads(d.get("citations_json") or "[]")
                    d["tasks_history"] = json.loads(d.get("tasks_history_json") or "[]")
                    d["github_pr_data"] = json.loads(d.get("github_pr_data_json") or "{}")
                    d["signoffs"] = json.loads(d.get("signoffs_json") or "{}")
                    matches.append(d)
            return matches

    def update_session(self, session_id: str, **kwargs):
        session = self.get_session_by_id(session_id)
        if not session:
            return

        session.update(kwargs)
        citations_json = json.dumps(kwargs.get("citations", session.get("citations", [])))
        tasks_history_json = json.dumps(kwargs.get("tasks_history", session.get("tasks_history", [])))
        github_pr_data_json = json.dumps(kwargs.get("github_pr_data", session.get("github_pr_data", {})))
        signoffs_json = json.dumps(kwargs.get("signoffs", session.get("signoffs", {})))

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE agent_sessions
                SET state = ?, active_plan_version = ?, container_id = ?,
                    last_plan_message_id = ?, last_completed_message_id = ?, header_message_id = ?,
                    review_message_id = ?, github_pr_data_json = ?, signoffs_json = ?,
                    pr_url = ?, pr_number = ?, citations_json = ?, tasks_history_json = ?,
                    task_type = ?, thread_title = ?, last_active_at = CURRENT_TIMESTAMP
                WHERE session_id = ?
            """, (
                session.get("state", "planning"),
                session.get("active_plan_version", 1),
                session.get("container_id", ""),
                session.get("last_plan_message_id", ""),
                session.get("last_completed_message_id", ""),
                session.get("header_message_id", ""),
                session.get("review_message_id", ""),
                github_pr_data_json,
                signoffs_json,
                session.get("pr_url", ""),
                int(session.get("pr_number", 0)),
                citations_json,
                tasks_history_json,
                session.get("task_type", "general"),
                session.get("thread_title", ""),
                session_id
            ))
            conn.commit()

    def record_signoff(
        self,
        session_id: str,
        user_id: str | int,
        user_name: str,
        git_name: str,
        git_email: str,
        commit_message: str,
        is_anonymous: bool = False
    ):
        session = self.get_session_by_id(session_id)
        if not session:
            return

        signoffs = session.get("signoffs", {})
        signoffs[str(user_id)] = {
            "user_id": str(user_id),
            "user_name": user_name,
            "git_name": git_name.strip(),
            "git_email": git_email.strip(),
            "commit_message": commit_message.strip(),
            "is_anonymous": is_anonymous,
            "signed_at": int(time.time())
        }

        pr_data = session.get("github_pr_data", {})
        if commit_message.strip():
            pr_data["commit_message"] = commit_message.strip()

        self.update_session(session_id, signoffs=signoffs, github_pr_data=pr_data)
        logger.info(f"[AgentSession] Recorded sign-off from {user_name} ({user_id}) for session #{session_id} (Anon: {is_anonymous})")

    def record_completed_task(
        self,
        session_id: str,
        task_num: int,
        objective: str,
        task_type: str,
        summary: str,
        deliverables: list[str]
    ):
        session = self.get_session_by_id(session_id)
        if not session:
            return

        history = session.get("tasks_history", [])
        history.append({
            "task_num": task_num,
            "objective": objective.strip(),
            "task_type": task_type,
            "summary": summary.strip()[:600],
            "deliverables": deliverables,
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        })

        self.update_session(session_id, tasks_history=history)
        logger.info(f"[AgentSession] Recorded completed task #{task_num} for session #{session_id} ({len(deliverables)} deliverable(s))")

    def is_collaborator(self, session: dict[str, Any], user_id: str | int, member_or_perms: Any = None) -> bool:
        uid = str(user_id)
        if uid == str(session.get("creator_id")):
            return True
        if uid in session.get("collaborators", []):
            return True
        if member_or_perms:
            perms = getattr(member_or_perms, "guild_permissions", member_or_perms)
            if perms and getattr(perms, "administrator", False):
                return True
        return False

    def save_step_log(
        self,
        session_id: str,
        step_index: int,
        tool_name: str,
        args: dict[str, Any],
        result: dict[str, Any],
        diff_text: str = "",
        additions: int = 0,
        deletions: int = 0,
        duration_ms: int = 0
    ):
        step_id = f"{session_id}_{step_index}"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO agent_step_logs (
                    step_id, session_id, step_index, tool_name,
                    args_json, result_json, diff_text, additions, deletions, duration_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(step_id) DO UPDATE SET
                    result_json = excluded.result_json,
                    diff_text = excluded.diff_text,
                    additions = excluded.additions,
                    deletions = excluded.deletions,
                    duration_ms = excluded.duration_ms
            """, (
                step_id,
                session_id,
                step_index,
                tool_name,
                json.dumps(args),
                json.dumps(result if isinstance(result, (dict, list)) else {"output": str(result)}),
                diff_text,
                additions,
                deletions,
                duration_ms
            ))
            conn.commit()

    def get_step_log(self, session_id: str, step_index: int | str) -> dict[str, Any] | None:
        step_id = f"{session_id}_{step_index}"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM agent_step_logs WHERE step_id = ?", (step_id,))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["name"] = d.get("tool_name")
                d["args"] = json.loads(d.get("args_json") or "{}")
                d["result"] = json.loads(d.get("result_json") or "{}")
                return d
        return None

    def save_session_thoughts(self, session_id: str, thoughts: str, tool_calls: list[dict[str, Any]], duration_seconds: int):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO agent_thoughts (session_id, thoughts, tool_calls_json, duration_seconds, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(session_id) DO UPDATE SET
                    thoughts = excluded.thoughts,
                    tool_calls_json = excluded.tool_calls_json,
                    duration_seconds = excluded.duration_seconds,
                    updated_at = CURRENT_TIMESTAMP
            """, (session_id, thoughts, json.dumps(tool_calls), duration_seconds))
            conn.commit()

    def get_session_thoughts(self, session_id: str) -> dict[str, Any] | None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM agent_thoughts WHERE session_id = ?", (session_id,))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["tool_calls"] = json.loads(d.get("tool_calls_json") or "[]")
                return d
        return None

    async def _download_repo_zip_fallback(self, repo_url: str, dest_dir: str, auth_token: str | None = None) -> bool:
        _, owner, repo = normalize_repo_url(repo_url)
        if not owner or not repo:
            return False

        zip_urls = [
            f"https://github.com/{owner}/{repo}/archive/refs/heads/main.zip",
            f"https://github.com/{owner}/{repo}/archive/refs/heads/master.zip"
        ]
        headers = {"User-Agent": "PriestyAI-Agent"}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        elif GITHUB_TOKEN:
            headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            for z_url in zip_urls:
                try:
                    resp = await client.get(z_url, headers=headers)
                    if resp.status_code == 200:
                        zf = zipfile.ZipFile(io.BytesIO(resp.content))
                        root_prefix = zf.namelist()[0].split("/")[0] if "/" in zf.namelist()[0] else ""
                        for member in zf.infolist():
                            if member.is_dir():
                                continue
                            rel_path = member.filename
                            if root_prefix and rel_path.startswith(root_prefix + "/"):
                                rel_path = rel_path[len(root_prefix) + 1:]
                            target_file = os.path.join(dest_dir, rel_path)
                            os.makedirs(os.path.dirname(target_file), exist_ok=True)
                            with open(target_file, "wb") as out_f:
                                out_f.write(zf.read(member.filename))
                        logger.info(f"[AgentDocker] Extracted {len(zf.namelist())} files into {dest_dir}")
                        return True
                except Exception as e:
                    logger.warning(f"[AgentDocker] Zip fallback failed for {z_url}: {e}")
        return False

    async def ensure_workspace_cloned(self, session: dict[str, Any]) -> bool:
        workspace_path = session["workspace_path"]
        repo_url = session.get("repo_url", "").strip()

        if not repo_url:
            if shutil.which("git") and not os.path.exists(os.path.join(workspace_path, ".git")):
                try:
                    init_proc = await asyncio.create_subprocess_exec(
                        "git", "init",
                        cwd=workspace_path,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL
                    )
                    await init_proc.communicate()
                    await self._configure_git_identity_local(workspace_path)
                except Exception:
                    pass
            return False

        clone_url, owner, repo = normalize_repo_url(repo_url)
        target_clone_url = clone_url or repo_url

        from core.github_app_client import github_app_client
        inst_token, _ = await github_app_client.get_installation_token_for_repo(owner, repo) if (owner and repo) else (None, None)

        if inst_token:
            auth_clone_url = f"https://x-access-token:{inst_token}@github.com/{owner}/{repo}.git"
            logger.info(f"[AgentDocker] Cloning {owner}/{repo} using GitHub App Installation Token...")
        elif GITHUB_TOKEN and owner and repo:
            auth_clone_url = f"https://{GITHUB_TOKEN}@github.com/{owner}/{repo}.git"
            logger.info(f"[AgentDocker] Cloning {owner}/{repo} using GITHUB_TOKEN...")
        else:
            auth_clone_url = target_clone_url
            logger.info(f"[AgentDocker] Cloning public repo {owner}/{repo}...")

        if not os.listdir(workspace_path):
            cloned = False
            if shutil.which("git"):
                try:
                    clone_cmd = ["git", "clone", "--depth", "1", auth_clone_url, "."]
                    clone_proc = await asyncio.create_subprocess_exec(
                        *clone_cmd,
                        cwd=workspace_path,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    stdout, stderr = await asyncio.wait_for(clone_proc.communicate(), timeout=30.0)
                    if clone_proc.returncode == 0:
                        cloned = True
                        await self._configure_git_identity_local(workspace_path)
                        logger.info(f"[AgentDocker] Cloned {owner}/{repo} into {workspace_path}")
                    else:
                        logger.warning(f"[AgentDocker] git clone error: {stderr.decode()}")
                except Exception as ex:
                    logger.warning(f"[AgentDocker] git clone error: {ex}")

            if not cloned:
                logger.info(f"[AgentDocker] Falling back to HTTP zip download for {owner}/{repo}...")
                await self._download_repo_zip_fallback(target_clone_url, workspace_path, auth_token=inst_token)
                if shutil.which("git") and not os.path.exists(os.path.join(workspace_path, ".git")):
                    try:
                        init_proc = await asyncio.create_subprocess_exec(
                            "git", "init",
                            cwd=workspace_path,
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL
                        )
                        await init_proc.communicate()
                        await self._configure_git_identity_local(workspace_path)
                    except Exception:
                        pass

        return True

    async def _configure_git_identity_local(self, workspace_path: str):
        if not shutil.which("git"):
            return
        cmds = [
            ["git", "config", "user.name", GITHUB_BOT_NAME],
            ["git", "config", "user.email", GITHUB_BOT_EMAIL]
        ]
        for cmd in cmds:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    cwd=workspace_path,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL
                )
                await proc.communicate()
            except Exception:
                pass

    async def ensure_docker_container(self, session: dict[str, Any]) -> str:
        session_id = session["session_id"]
        workspace_path = session["workspace_path"]
        container_name = f"priesty_agent_{session_id}"

        if not shutil.which("docker"):
            logger.warning("[AgentDocker] Docker CLI not found. Running commands directly on host workspace.")
            return ""

        try:
            inspect_proc = await asyncio.create_subprocess_exec(
                "docker", "inspect", "-f", "{{.State.Running}}", container_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(inspect_proc.communicate(), timeout=3.0)
            if inspect_proc.returncode == 0 and "true" in stdout.decode().lower():
                return container_name
            else:
                rm_proc = await asyncio.create_subprocess_exec("docker", "rm", "-f", container_name, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                await rm_proc.communicate()
        except Exception:
            pass

        selected_image = PRIMARY_POLYGLOT_IMAGE
        docker_cmd = [
            "docker", "run", "-d",
            "--name", container_name,
            "--memory=1024m",
            "--cpus=1.5",
            "-v", f"{workspace_path}:/workspace",
            "-w", "/workspace",
            selected_image,
            "tail", "-f", "/dev/null"
        ]

        logger.info(f"[AgentDocker] Launching polyglot workspace container {container_name} ({selected_image})...")
        try:
            run_proc = await asyncio.create_subprocess_exec(
                *docker_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(run_proc.communicate(), timeout=90.0)
            if run_proc.returncode != 0:
                logger.warning(f"[AgentDocker] Primary image failed ({stderr.decode()}). Falling back to {FALLBACK_DOCKER_IMAGE}...")
                fallback_cmd = list(docker_cmd)
                fallback_cmd[-2] = FALLBACK_DOCKER_IMAGE
                run_proc = await asyncio.create_subprocess_exec(
                    *fallback_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await asyncio.wait_for(run_proc.communicate(), timeout=90.0)

            if run_proc.returncode == 0:
                self.update_session(session_id, container_id=container_name)
                
                setup_cmds = [
                    f"git config --global user.name '{GITHUB_BOT_NAME}'",
                    f"git config --global user.email '{GITHUB_BOT_EMAIL}'",
                    "git config --global --add safe.directory /workspace"
                ]
                for sc in setup_cmds:
                    try:
                        c_proc = await asyncio.create_subprocess_exec(
                            "docker", "exec", container_name, "sh", "-c", sc,
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL
                        )
                        await c_proc.communicate()
                    except Exception:
                        pass

                logger.info(f"[AgentDocker] Polyglot workspace container online: {container_name}")
                return container_name
            else:
                logger.warning(f"[AgentDocker] Docker run returned {run_proc.returncode}: {stderr.decode()}")
        except Exception as e:
            logger.error(f"[AgentDocker] Container launch failed or timed out: {e}")

        return ""

    async def exec_in_container(self, session_id: str, cmd_str: str, timeout: float = 60.0) -> tuple[int, str, str]:
        session = self.get_session_by_id(session_id)
        if not session:
            return -1, "", "Session not found."

        container_name = session.get("container_id", "")
        workspace_path = session["workspace_path"]
        has_docker = shutil.which("docker") is not None and bool(container_name)

        if has_docker:
            exec_args = ["docker", "exec", "-w", "/workspace", container_name, "sh", "-c", cmd_str]
            cwd = None
        else:
            if sys.platform == "win32":
                py_exe = sys.executable
                if cmd_str.startswith("python3 ") or cmd_str.startswith("python "):
                    cleaned_cmd = cmd_str.split(" ", 1)[-1]
                    exec_args = [py_exe, "-c", cleaned_cmd] if "-c" in cleaned_cmd else [py_exe] + cleaned_cmd.split()
                elif cmd_str.startswith("pytest ") or cmd_str == "pytest":
                    args_part = cmd_str.split("pytest", 1)[-1].strip()
                    exec_args = [py_exe, "-m", "pytest"] + (args_part.split() if args_part else [])
                elif cmd_str.startswith("pip ") or cmd_str.startswith("pip3 "):
                    args_part = cmd_str.split(" ", 1)[-1].strip()
                    exec_args = [py_exe, "-m", "pip"] + args_part.split()
                elif cmd_str.startswith("npm ") or cmd_str.startswith("npx ") or cmd_str.startswith("node "):
                    exec_args = ["cmd.exe", "/c", cmd_str]
                else:
                    exec_args = ["cmd.exe", "/c", cmd_str]
            else:
                exec_args = ["sh", "-c", cmd_str]
            cwd = workspace_path

        try:
            proc = await asyncio.create_subprocess_exec(
                *exec_args,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            out_str = stdout_b.decode("utf-8", errors="replace").strip()
            err_str = stderr_b.decode("utf-8", errors="replace").strip()
            return proc.returncode, out_str, err_str
        except asyncio.TimeoutError:
            return -1, "", f"Command timed out after {timeout} seconds."
        except Exception as e:
            return -1, "", str(e)

    async def stop_session_container(self, session_id: str):
        container_name = f"priesty_agent_{session_id}"
        if shutil.which("docker"):
            try:
                proc = await asyncio.create_subprocess_exec(
                    "docker", "rm", "-f", container_name,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL
                )
                await asyncio.wait_for(proc.communicate(), timeout=5.0)
                logger.info(f"[AgentDocker] Terminated container {container_name}")
            except Exception:
                pass

    async def cleanup_session(self, session_id: str, delete_workspace: bool = True):
        await self.stop_session_container(session_id)
        if delete_workspace:
            workspace_dir = os.path.join(AGENT_WORKSPACES_ROOT, session_id)
            if os.path.exists(workspace_dir):
                try:
                    shutil.rmtree(workspace_dir, ignore_errors=True)
                    logger.info(f"[AgentSession] Cleaned up workspace directory: {workspace_dir}")
                except Exception as e:
                    logger.warning(f"[AgentSession] Failed to delete workspace directory: {e}")

    async def prune_stale_workspaces(self, max_age_seconds: int = 86400):
        now = time.time()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT session_id, workspace_path, last_active_at FROM agent_sessions")
            rows = cursor.fetchall()

        for r in rows:
            sid = r["session_id"]
            w_path = r["workspace_path"]
            if os.path.exists(w_path):
                try:
                    mtime = os.path.getmtime(w_path)
                    if (now - mtime) > max_age_seconds:
                        logger.info(f"[AgentSession] Pruning stale workspace #{sid} (>24h inactive)...")
                        await self.cleanup_session(sid, delete_workspace=True)
                except Exception as e:
                    logger.debug(f"Error checking workspace mtime: {e}")

session_manager = AgentSessionManager()