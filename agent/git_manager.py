import os
import re
import json
import asyncio
import logging
from typing import Any
from google.genai import types

from agent.session_manager import normalize_repo_url
from agent.constants import GITHUB_BOT_NAME
from config.settings import GITHUB_APP_BOT_NAME, GITHUB_APP_BOT_EMAIL
from core.github_app_client import github_app_client
from core.client_manager import client_manager

logger = logging.getLogger("PriestyAI.Agent.GitManager")

SCRATCHPAD_EXCLUSIONS = {
    "plan.md",
    "research_plan.md",
    "report.html",
    "research_report.html",
    "study_guide.md",
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".idea",
    ".vscode"
}

def is_scratchpad_file(filepath: str) -> bool:
    clean = filepath.replace("\\", "/").strip().lstrip("./")
    parts = clean.split("/")
    if any(p in SCRATCHPAD_EXCLUSIONS for p in parts):
        return True
    base_name = os.path.basename(clean).lower()
    return base_name in SCRATCHPAD_EXCLUSIONS

class GitManager:

    @staticmethod
    async def detect_code_changes(workspace_path: str) -> tuple[bool, list[str], dict[str, Any]]:
        if not os.path.exists(workspace_path):
            return False, [], {"additions": 0, "deletions": 0}

        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "status", "--porcelain",
                cwd=workspace_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=6.0)
            status_lines = stdout.decode("utf-8", errors="replace").splitlines()

            code_files: list[str] = []
            for line in status_lines:
                clean_line = line.strip()
                if not clean_line:
                    continue
                parts = clean_line.split(maxsplit=1)
                if len(parts) >= 2:
                    filepath = parts[1].strip().strip('"')
                    if not is_scratchpad_file(filepath):
                        code_files.append(filepath)

            if not code_files:
                return False, [], {"additions": 0, "deletions": 0}

            diff_proc = await asyncio.create_subprocess_exec(
                "git", "diff", "--shortstat",
                cwd=workspace_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )
            diff_out, _ = await asyncio.wait_for(diff_proc.communicate(), timeout=4.0)
            stat_text = diff_out.decode("utf-8", errors="replace")

            adds, dels = 0, 0
            adds_m = re.search(r'(\d+)\s+insertion', stat_text)
            dels_m = re.search(r'(\d+)\s+deletion', stat_text)
            if adds_m:
                adds = int(adds_m.group(1))
            if dels_m:
                dels = int(dels_m.group(1))

            return True, code_files, {"additions": adds, "deletions": dels, "total_files": len(code_files)}

        except Exception as e:
            logger.warning(f"[GitManager] Status detection failed: {e}")
            return False, [], {"additions": 0, "deletions": 0}

    @staticmethod
    def format_feature_branch_name(prompt_or_title: str) -> str:
        clean = re.sub(r'[^a-zA-Z0-9\s_-]+', '', prompt_or_title).strip()
        slug = re.sub(r'[\s_]+', '-', clean).strip('-').lower()
        truncated = "-".join(slug.split("-")[:5]) or "feature-update"
        return f"priestyai/{truncated}"

    @staticmethod
    async def generate_pr_metadata(
        initial_prompt: str,
        changed_files: list[str],
        task_summary: str = ""
    ) -> tuple[str, str, str]:
        client, key_idx, active_model = client_manager.get_client_for_model("gemini-3.5-flash-lite", fallback=True)
        default_commit = f"feat: implement {initial_prompt[:45]}"
        default_pr_title = f"feat: {initial_prompt[:60]}"
        default_pr_desc = f"## Overview\n{task_summary or initial_prompt}\n\n### Modified Files\n" + "\n".join([f"- `{f}`" for f in changed_files[:10]])

        if not client:
            return default_commit, default_pr_title, default_pr_desc

        instruction = (
            "You are PriestyAI. Draft GitHub Pull Request metadata for these workspace code changes.\n"
            "Output strict JSON with:\n"
            "1. commit_message: Conventional commit message (e.g. 'feat(auth): implement PKCE flow').\n"
            "2. pr_title: Clean PR title (max 70 chars).\n"
            "3. pr_description: Professional markdown PR body with ## Summary, ## Changes, and ## Verification.\n"
            "JSON Format: {\"commit_message\": \"...\", \"pr_title\": \"...\", \"pr_description\": \"...\"}"
        )

        prompt_content = (
            f"Objective: {initial_prompt}\n"
            f"Summary of work: {task_summary}\n"
            f"Changed Files:\n" + "\n".join([f"- {f}" for f in changed_files[:15]])
        )

        try:
            res = await client.aio.models.generate_content(
                model=active_model,
                contents=prompt_content,
                config=types.GenerateContentConfig(
                    system_instruction=instruction,
                    response_mime_type="application/json",
                    temperature=0.2
                )
            )
            if res.text:
                data = json.loads(res.text.strip())
                return (
                    data.get("commit_message", default_commit),
                    data.get("pr_title", default_pr_title),
                    data.get("pr_description", default_pr_desc)
                )
        except Exception as e:
            logger.debug(f"[GitManager] LLM PR metadata generation fallback: {e}")

        return default_commit, default_pr_title, default_pr_desc

    @staticmethod
    async def stage_and_commit(
        workspace_path: str,
        commit_message: str,
        signoffs: list[dict[str, Any]]
    ) -> tuple[bool, str]:
        await asyncio.create_subprocess_exec("git", "config", "user.name", GITHUB_APP_BOT_NAME, cwd=workspace_path)
        await asyncio.create_subprocess_exec("git", "config", "user.email", GITHUB_APP_BOT_EMAIL, cwd=workspace_path)

        commit_body_lines = [commit_message.strip(), ""]
        for s in signoffs:
            g_name = s.get("git_name", "").strip()
            g_email = s.get("git_email", "").strip()
            if g_name and g_email and "@" in g_email:
                commit_body_lines.append(f"Co-authored-by: {g_name} <{g_email}>")

        full_commit_text = "\n".join(commit_body_lines).strip()

        try:
            _, code_files, _ = await GitManager.detect_code_changes(workspace_path)
            if not code_files:
                return False, "No code changes detected to commit."

            for f_path in code_files:
                await asyncio.create_subprocess_exec("git", "add", f_path, cwd=workspace_path)

            commit_proc = await asyncio.create_subprocess_exec(
                "git", "commit", "-m", full_commit_text,
                cwd=workspace_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(commit_proc.communicate(), timeout=8.0)
            if commit_proc.returncode == 0:
                logger.info(f"[GitManager] Successfully committed {len(code_files)} code file(s).")
                return True, "Commit created successfully."
            else:
                return False, f"Git commit failed: {stderr.decode()}"

        except Exception as e:
            logger.error(f"[GitManager] Commit execution failed: {e}")
            return False, str(e)

    @staticmethod
    async def publish_branch_and_create_pr(
        workspace_path: str,
        repo_url: str,
        branch_name: str,
        pr_title: str,
        pr_body: str,
        token: str
    ) -> dict[str, Any]:
        _, owner, repo = normalize_repo_url(repo_url)
        if not owner or not repo:
            return {"error": f"Invalid repository URL '{repo_url}'."}

        auth_remote_url = f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"

        try:
            await asyncio.create_subprocess_exec("git", "branch", "-M", branch_name, cwd=workspace_path)

            logger.info(f"[GitManager] Pushing branch '{branch_name}' to {owner}/{repo} via Installation Token...")
            push_proc = await asyncio.create_subprocess_exec(
                "git", "push", "-u", auth_remote_url, branch_name, "--force",
                cwd=workspace_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(push_proc.communicate(), timeout=30.0)

            if push_proc.returncode != 0:
                err_str = stderr.decode("utf-8", errors="replace")
                logger.error(f"[GitManager] git push failed ({push_proc.returncode}): {err_str}")
                return {"error": f"Failed to push branch to GitHub: {err_str}"}

            base_branch = await github_app_client.get_default_branch(owner, repo, token=token)

            pr_res = await github_app_client.create_pull_request(
                owner=owner,
                repo=repo,
                title=pr_title,
                body=pr_body,
                head_branch=branch_name,
                base_branch=base_branch,
                token=token
            )
            return pr_res

        except Exception as e:
            logger.error(f"[GitManager] Push and PR failed: {e}")
            return {"error": f"Failed to publish branch: {str(e)}"}

git_manager = GitManager()