import os
import re
import json
import asyncio
import logging
from typing import Any
from google.genai import types

from agent.session_manager import normalize_repo_url
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

NOISE_WORDS = {
    "implement", "implementation", "create", "creating", "add", "adding",
    "build", "building", "make", "making", "write", "writing", "setup",
    "fix", "fixing", "update", "updating", "refactor", "refactoring",
    "and", "the", "a", "an", "in", "for", "with", "of", "to", "on"
}

def is_scratchpad_file(filepath: str) -> bool:
    clean = filepath.replace("\\", "/").strip().lstrip("./")
    parts = clean.split("/")
    if any(p in SCRATCHPAD_EXCLUSIONS for p in parts):
        return True
    base_name = os.path.basename(clean).lower()
    return base_name in SCRATCHPAD_EXCLUSIONS

def clean_stale_git_locks(workspace_path: str):
    lock_file = os.path.join(workspace_path, ".git", "index.lock")
    if os.path.exists(lock_file):
        try:
            os.remove(lock_file)
            logger.info(f"[GitManager] Cleaned up stale lock file: {lock_file}")
        except Exception as e:
            logger.warning(f"[GitManager] Could not remove stale index.lock: {e}")

class GitManager:

    @staticmethod
    async def detect_code_changes(workspace_path: str) -> tuple[bool, list[str], dict[str, Any]]:
        if not os.path.exists(workspace_path):
            return False, [], {"additions": 0, "deletions": 0}

        clean_stale_git_locks(workspace_path)

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
        clean = re.sub(r'[^a-zA-Z\s_-]+', '', prompt_or_title).strip()
        words = [w.lower() for w in re.split(r'[\s_-]+', clean) if w.lower() not in NOISE_WORDS and len(w) >= 3]

        if words:
            slug = "-".join(words[:2])[:22].rstrip("-")
        else:
            slug = "feature-update"

        return f"priestyai/{slug}"

    @staticmethod
    async def generate_pr_metadata(
        initial_prompt: str,
        changed_files: list[str],
        task_summary: str = ""
    ) -> tuple[str, str, str, str]:
        client, key_idx, active_model = client_manager.get_client_for_model("gemini-3.5-flash-lite", fallback=True)
        fallback_branch = GitManager.format_feature_branch_name(initial_prompt)
        default_commit = f"feat: implement {initial_prompt[:45]}"
        default_pr_title = f"feat: {initial_prompt[:60]}"
        default_pr_desc = f"## Overview\n{task_summary or initial_prompt}\n\n### Modified Files\n" + "\n".join([f"- `{f}`" for f in changed_files[:10]])

        if not client:
            return default_commit, default_pr_title, default_pr_desc, fallback_branch

        instruction = (
            "You are PriestyAI. Draft GitHub Pull Request metadata for these workspace code changes.\n"
            "Output strict JSON with:\n"
            "1. branch_slug: A crisp 2 to 3 word kebab-case branch slug (max 20 chars, e.g. 'tiered-discounts' or 'order-service').\n"
            "2. commit_message: Conventional commit message (e.g. 'feat(order): implement tiered discounts and sales tax').\n"
            "3. pr_title: Clean PR title (max 65 chars).\n"
            "4. pr_description: Professional markdown PR body with ## Summary, ## Changes, and ## Verification.\n"
            "JSON Format: {\"branch_slug\": \"...\", \"commit_message\": \"...\", \"pr_title\": \"...\", \"pr_description\": \"...\"}"
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
                slug = data.get("branch_slug", "").strip()
                clean_slug = re.sub(r'[^a-zA-Z0-9_-]+', '', slug).strip('-').lower()[:20]
                branch_final = f"priestyai/{clean_slug}" if clean_slug else fallback_branch

                return (
                    data.get("commit_message", default_commit),
                    data.get("pr_title", default_pr_title),
                    data.get("pr_description", default_pr_desc),
                    branch_final
                )
        except Exception as e:
            logger.debug(f"[GitManager] LLM PR metadata generation fallback: {e}")

        return default_commit, default_pr_title, default_pr_desc, fallback_branch

    @staticmethod
    def build_commit_message_with_coauthors(commit_message: str, signoffs: list[dict[str, Any]]) -> str:
        commit_body_lines = [commit_message.strip(), ""]
        for s in signoffs:
            g_name = s.get("git_name", "").strip()
            g_email = s.get("git_email", "").strip()
            if g_name and g_email and "@" in g_email and not s.get("is_anonymous"):
                commit_body_lines.append(f"Co-authored-by: {g_name} <{g_email}>")

        return "\n".join(commit_body_lines).strip()

    @staticmethod
    async def stage_and_commit(
        workspace_path: str,
        commit_message: str,
        signoffs: list[dict[str, Any]],
        branch_name: str | None = None
    ) -> tuple[bool, str]:
        clean_stale_git_locks(workspace_path)
        bot_name = github_app_client.get_bot_name()
        bot_email = github_app_client.get_bot_email()

        cfg_u = await asyncio.create_subprocess_exec("git", "config", "user.name", bot_name, cwd=workspace_path)
        await cfg_u.communicate()

        cfg_e = await asyncio.create_subprocess_exec("git", "config", "user.email", bot_email, cwd=workspace_path)
        await cfg_e.communicate()

        if branch_name:
            br_proc = await asyncio.create_subprocess_exec("git", "checkout", "-B", branch_name, cwd=workspace_path)
            await br_proc.communicate()

        full_commit_text = GitManager.build_commit_message_with_coauthors(commit_message, signoffs)

        try:
            _, code_files, _ = await GitManager.detect_code_changes(workspace_path)
            if not code_files:
                return True, "Already committed or up to date."

            for f_path in code_files:
                add_proc = await asyncio.create_subprocess_exec("git", "add", f_path, cwd=workspace_path)
                await add_proc.communicate()

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
                err_text = stderr.decode().strip()
                if "nothing to commit" in err_text.lower():
                    return True, "Already up to date."
                return False, f"Git commit failed: {err_text}"

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
        signoffs: list[dict[str, Any]],
        token: str,
        changed_files: list[str] | None = None
    ) -> dict[str, Any]:
        _, owner, repo = normalize_repo_url(repo_url)
        if not owner or not repo:
            return {"error": f"Invalid repository URL '{repo_url}'."}

        code_files = list(changed_files or [])
        if not code_files:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD",
                    cwd=workspace_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL
                )
                stdout, _ = await proc.communicate()
                lines = [l.strip() for l in stdout.decode("utf-8", errors="replace").splitlines() if l.strip()]
                code_files = [f for f in lines if not is_scratchpad_file(f)]
            except Exception:
                pass

        if not code_files:
            _, uncommitted, _ = await GitManager.detect_code_changes(workspace_path)
            code_files = uncommitted

        files_payload: list[dict[str, str]] = []
        for rel_path in code_files:
            full_f_path = os.path.join(workspace_path, rel_path)
            if os.path.exists(full_f_path):
                try:
                    with open(full_f_path, "r", encoding="utf-8", errors="replace") as f_obj:
                        files_payload.append({
                            "path": rel_path.replace("\\", "/"),
                            "content": f_obj.read()
                        })
                except Exception as read_err:
                    logger.warning(f"[GitManager] Could not read file {rel_path}: {read_err}")

        base_branch = await github_app_client.get_default_branch(owner, repo, token=token)
        full_commit_message = GitManager.build_commit_message_with_coauthors(pr_title, signoffs)

        if files_payload:
            logger.info(f"[GitManager] Creating API-signed Verified Commit for {owner}/{repo} on branch '{branch_name}' ({len(files_payload)} file(s))...")
            api_res = await github_app_client.create_api_verified_commit_and_branch(
                owner=owner,
                repo=repo,
                branch_name=branch_name,
                base_branch=base_branch,
                commit_message=full_commit_message,
                files_data=files_payload,
                token=token
            )
        else:
            api_res = {"error": "No file payloads for API commit, using CLI push."}

        if "error" in api_res:
            logger.info(f"[GitManager] Using authenticated Git CLI push for branch '{branch_name}'...")
            clean_stale_git_locks(workspace_path)
            auth_remote_url = f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"
            try:
                br_proc = await asyncio.create_subprocess_exec("git", "checkout", "-B", branch_name, cwd=workspace_path)
                await br_proc.communicate()

                push_proc = await asyncio.create_subprocess_exec(
                    "git", "push", "-u", auth_remote_url, f"{branch_name}:{branch_name}", "--force",
                    cwd=workspace_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await asyncio.wait_for(push_proc.communicate(), timeout=30.0)
                if push_proc.returncode != 0:
                    return {"error": f"Push failed: {stderr.decode()}"}
            except Exception as cli_err:
                return {"error": f"CLI push fallback failed: {cli_err}"}

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

git_manager = GitManager()