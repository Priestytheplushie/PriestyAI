import io
import logging
import os
import shutil
import tempfile
import zipfile
from typing import Any, Dict, List
import httpx
from app.config import settings
from app.core.docker_runner import docker_runner
from app.github.client import AppInstallationClient, machine_client
from app.llm.client import llm_client

logger = logging.getLogger("priesty.resolve_conflicts")

EXTENSION_LANG_MAP = {
    ".py": "python",
    ".ts": "typescript",
    ".js": "javascript",
    ".rs": "rust",
    ".go": "go",
    ".html": "html",
    ".css": "css",
    ".json": "json",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".md": "markdown",
}


def get_lang_tag(path: str) -> str:
    _, ext = os.path.splitext(path)
    return EXTENSION_LANG_MAP.get(ext.lower(), "")


async def handle_resolve_conflicts(
    installation_id: int,
    owner: str,
    repo: str,
    pull_number: int,
    requester_login: str,
    user_prompt: str,
) -> None:
    app_client = AppInstallationClient(installation_id)
    pr = await app_client.get_pull_request(owner, repo, pull_number)

    head_ref = pr.get("head", {}).get("ref")
    base_ref = pr.get("base", {}).get("ref", "main")
    head_sha = pr.get("head", {}).get("sha")

    base_sha = await app_client.get_branch_sha(owner, repo, base_ref)

    caller_perm = await app_client.get_user_permission(owner, repo, requester_login)
    is_maintainer = caller_perm in ("admin", "write", "maintain")
    pr_author = pr.get("user", {}).get("login", "")
    is_pr_author = requester_login.lower() == pr_author.lower()
    bot_authored_pr = pr_author.lower() == settings.BOT_USERNAME.lower()

    head_repo = pr.get("head", {}).get("repo", {})
    base_repo = pr.get("base", {}).get("repo", {})
    is_fork = head_repo.get("full_name") != base_repo.get("full_name")
    maintainer_can_modify = pr.get("maintainer_can_modify", True)

    can_modify_branch = not is_fork or maintainer_can_modify
    is_authorized_caller = is_maintainer or is_pr_author or bot_authored_pr
    can_commit = is_authorized_caller and can_modify_branch

    logger.info(
        f"Conflict resolution requested for {owner}/{repo}#{pull_number} ({base_ref} -> {head_ref}) by @{requester_login}. "
        f"is_maintainer={is_maintainer}, is_pr_author={is_pr_author}, can_commit={can_commit}"
    )

    files_changed = await app_client.get_pull_request_files(owner, repo, pull_number)
    file_tree = await app_client.get_repository_tree(owner, repo, head_sha)
    contributing = (
        await app_client.get_file_content(owner, repo, "CONTRIBUTING.md", base_ref)
        or ""
    )

    stack_plan = await detect_repo_stack(file_tree)
    files_to_resolve: List[str] = [f["filename"] for f in files_changed]

    resolved_files: Dict[str, str] = {}
    fallback_sections: List[str] = []

    for path in files_to_resolve[:6]:
        head_file_info = await app_client.get_file_content_and_sha(
            owner, repo, path=path, ref=head_ref
        )
        head_content = head_file_info.get("content")
        base_content = await app_client.get_file_content(
            owner, repo, path=path, ref=base_ref
        )

        if head_content is None or base_content is None:
            continue

        if head_content.strip() == base_content.strip():
            continue

        merge_prompt = f"""You are an expert software engineer resolving a merge conflict.
You must merge the changes from the incoming base branch ('{base_ref}') into the PR head branch ('{head_ref}') for '{path}'.

RULES:
1. Preserve BOTH features/modifications cleanly from both sides.
2. Adhere strictly to the repository standards in CONTRIBUTING.md.
3. Eliminate duplicate imports or redundant statements.
4. Output ONLY the complete merged file content. No markdown fences, no commentary.

CONTRIBUTING GUIDELINES (FROM TRUSTED BASE BRANCH):
{contributing}

=== INCOMING '{base_ref}' BRANCH VERSION ===
```
{base_content}
```

=== PR '{head_ref}' BRANCH VERSION ===
```
{head_content}
```
"""

        merged_code = await llm_client.generate(
            prompt=merge_prompt,
            system_prompt="You are a principal engineer resolving complex merge conflicts cleanly. Output raw code only.",
            model_tier="reasoning",
        )
        merged_code = strip_markdown_fences(merged_code)
        lang_tag = get_lang_tag(path)

        if can_commit:
            if (
                stack_plan.get("has_automated_checks")
                and stack_plan.get("docker_image")
                and docker_runner.available
            ):
                if is_maintainer or not is_fork:
                    verified, test_err = await verify_in_docker(
                        app_client, owner, repo, head_ref, path, merged_code, stack_plan
                    )
                    if not verified:
                        logger.warning(
                            f"Verification warning on merged {path}: {test_err}"
                        )

            resolved_files[path] = merged_code
        else:
            fallback_sections.append(
                f"### Resolved `<code>{path}</code>`\n<details><summary>View merged code</summary>\n\n```{lang_tag}\n{merged_code}\n```\n\n</details>"
            )

    if can_commit:
        if resolved_files:
            file_list = ", ".join([f"`{f}`" for f in resolved_files.keys()])

            await app_client.create_merge_commit(
                owner=owner,
                repo=repo,
                branch_name=head_ref,
                head_sha=head_sha,
                base_sha=base_sha,
                resolved_files=resolved_files,
                message=f"merge({head_ref}): resolve conflicts with {base_ref}",
            )

            reply_prompt = f"""You are PriestyAI, an engineer teammate.
You just resolved merge conflicts between '{base_ref}' and this PR branch for: {file_list}.
Write a brief, friendly teammate confirmation comment addressing @{requester_login}:
- Let them know conflicts were resolved cleanly and pushed to the branch in a merge commit.
- No emoji spam, no robotic phrases.
"""
            reply_text = await llm_client.generate(
                prompt=reply_prompt,
                system_prompt="You are a senior developer teammate. Speak naturally.",
                model_tier="routing",
            )
            await machine_client.create_issue_comment(
                owner, repo, pull_number, reply_text
            )
            logger.info(
                f"True 2-parent merge commit created and pushed for PR #{pull_number}"
            )
        else:
            await machine_client.create_issue_comment(
                owner=owner,
                repo=repo,
                issue_number=pull_number,
                body=f"I checked the files against `{base_ref}`, but didn't find any active conflicts to resolve.",
            )
    else:
        reason_msg = (
            "Because this PR is on an external fork branch without maintainer write access"
            if is_fork and not maintainer_can_modify
            else "Since this action requires repository write permissions or author status"
        )
        comment_body = (
            f"Here are the resolved files merged with `{base_ref}`! {reason_msg}, here is the code to apply:\n\n"
            + "\n\n".join(fallback_sections)
        )
        await machine_client.create_issue_comment(
            owner, repo, pull_number, comment_body
        )


async def detect_repo_stack(file_tree: List[str]) -> Dict[str, Any]:
    stack_prompt = f"""Analyze the repository file structure to determine the build and test environment.
Repository Files:
{file_tree[:60]}

Rules:
- Python with pytest/ruff: choose "python:3.11-slim" and pytest/ruff commands.
- Python with unittest: choose "python:3.11-slim" and python -m unittest discover.
- Node.js (package.json): choose "node:20-alpine" and npm test / npm run lint.
- Go (go.mod): choose "golang:1.22-alpine" and go test ./...
- Rust (Cargo.toml): choose "rust:1.78-alpine" and cargo test.
- Static assets, HTML, Markdown, or documentation without automated test runner: set "has_automated_checks": false and "docker_image": null.

Return JSON:
{{
  "has_automated_checks": true,
  "docker_image": "python:3.11-slim" or null,
  "commands": ["pytest"]
}}
"""
    return await llm_client.generate_json(
        prompt=stack_prompt,
        system_prompt="You are a senior devops engineer. Output valid JSON only.",
        model_tier="routing",
    )


async def verify_in_docker(
    app_client: AppInstallationClient,
    owner: str,
    repo: str,
    branch: str,
    target_path: str,
    candidate_code: str,
    stack_plan: Dict[str, Any],
) -> tuple[bool, str]:
    docker_image = stack_plan.get("docker_image")
    commands = stack_plan.get("commands", [])

    if not docker_image or not commands:
        return True, "No automated checks."

    temp_dir = tempfile.mkdtemp(prefix="priesty_conflict_verify_")
    try:
        zip_url = f"https://api.github.com/repos/{owner}/{repo}/zipball/{branch}"
        token = await app_client._get_headers()
        async with httpx.AsyncClient(follow_redirects=True) as http_client:
            r = await http_client.get(zip_url, headers=token)
            if r.status_code == 200:
                z = zipfile.ZipFile(io.BytesIO(r.content))
                root_folder = z.namelist()[0]
                z.extractall(temp_dir)
                extracted_path = os.path.join(temp_dir, root_folder)

                dest = os.path.join(extracted_path, target_path)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "w", encoding="utf-8") as f:
                    f.write(candidate_code)

                res = await docker_runner.run_commands(
                    workspace_dir=extracted_path,
                    commands=commands,
                    image=docker_image,
                )
                return (
                    res["success"],
                    f"STDOUT:\n{res['stdout']}\nSTDERR:\n{res['stderr']}",
                )
    except Exception as e:
        return True, str(e)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return True, "No verification output."


def strip_markdown_fences(code: str) -> str:
    if code.startswith("```"):
        lines = code.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines) + "\n"
    return code
